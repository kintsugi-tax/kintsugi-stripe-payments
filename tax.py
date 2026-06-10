import json
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from kintsugi_tax_platform_sdk import SDK, errors, models

from logger import logger
from schemas import Address, CreatePaymentIntentRequest, CustomerInfo, LineItem

STRIPE_METADATA_VALUE_LIMIT = 500
ESTIMATE_ITEMS_METADATA_KEY = "estimate_items"


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def compact_estimate_item(
    item: models.TransactionItemEstimateResponse,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "p": item.external_product_id,
        "q": float(item.quantity or 1),
        "a": float(item.amount),
    }
    if item.product_name:
        entry["n"] = item.product_name

    tax_amount = parse_optional_float(item.tax_amount)
    tax_rate = parse_optional_float(item.tax_rate)
    taxable_amount = parse_optional_float(item.taxable_amount)
    if tax_amount is not None:
        entry["t"] = tax_amount
    if tax_rate is not None:
        entry["r"] = tax_rate
    if taxable_amount is not None:
        entry["tb"] = taxable_amount
    return entry


def expand_compact_estimate_item(compact: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_product_id": compact.get("p", ""),
        "product_name": compact.get("n"),
        "quantity": compact.get("q", 1),
        "amount": compact.get("a"),
        "tax_amount": compact.get("t"),
        "tax_rate": compact.get("r"),
        "taxable_amount": compact.get("tb"),
    }


def encode_stripe_metadata_chunks(key: str, value: str) -> dict[str, str]:
    if len(value) <= STRIPE_METADATA_VALUE_LIMIT:
        return {key: value}

    chunks: dict[str, str] = {}
    for index in range(0, len(value), STRIPE_METADATA_VALUE_LIMIT):
        chunk_index = index // STRIPE_METADATA_VALUE_LIMIT
        chunks[f"{key}_{chunk_index}"] = value[
            index : index + STRIPE_METADATA_VALUE_LIMIT
        ]
    chunks[f"{key}_parts"] = str(len(chunks))
    return chunks


def decode_stripe_metadata_chunks(metadata: dict[str, Any], key: str) -> str | None:
    if key in metadata:
        return str(metadata[key])

    parts_key = f"{key}_parts"
    if parts_key not in metadata:
        return None

    part_count = int(metadata[parts_key])
    return "".join(str(metadata.get(f"{key}_{index}", "")) for index in range(part_count))


def parse_estimate_items_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    raw = decode_stripe_metadata_chunks(metadata, ESTIMATE_ITEMS_METADATA_KEY)
    if raw:
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid estimate_items metadata")
            return []
        if isinstance(items, list):
            return [expand_compact_estimate_item(item) for item in items]
        return []

    legacy_raw = decode_stripe_metadata_chunks(metadata, "estimate_json")
    if not legacy_raw:
        return []
    try:
        legacy = json.loads(legacy_raw)
    except json.JSONDecodeError:
        logger.warning("Invalid estimate_json metadata")
        return []

    items = legacy.get("transaction_items", [])
    return items if isinstance(items, list) else []


def parse_estimate_summary_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    legacy_raw = decode_stripe_metadata_chunks(metadata, "estimate_json")
    legacy: dict[str, Any] = {}
    if legacy_raw:
        try:
            legacy = json.loads(legacy_raw)
        except json.JSONDecodeError:
            legacy = {}

    return {
        "tax_rate_calculated": metadata.get("tax_rate") or legacy.get("tax_rate_calculated"),
        "total_tax_amount_calculated": legacy.get("total_tax_amount_calculated"),
        "taxable_amount": metadata.get("taxable_amount") or legacy.get("taxable_amount"),
    }


def dollars_to_cents(amount: str | float) -> int:
    return int(
        (Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def build_customer(
    customer: CustomerInfo,
) -> models.CustomerBasePublic | None:
    data = customer.model_dump(exclude_none=True)
    if not data:
        return None
    return models.CustomerBasePublic(**data)


def build_transaction_items(
    line_items: list[LineItem], external_id: str, now: datetime
) -> list[models.TransactionItemEstimateBaseTypedDict]:
    items: list[models.TransactionItemEstimateBaseTypedDict] = []
    for index, item in enumerate(line_items):
        entry: models.TransactionItemEstimateBaseTypedDict = {
            "external_id": f"{external_id}_{index}",
            "date_": now,
            "external_product_id": item.external_product_id,
            "quantity": item.quantity,
            "amount": item.amount,
        }
        if item.description:
            entry["description"] = item.description
        if item.product_name:
            entry["product_name"] = item.product_name
        items.append(entry)
    return items


def build_addresses(
    address: Address,
) -> list[models.TransactionEstimatePublicRequestAddressTypedDict]:
    return [
        {
            "type": models.TransactionEstimatePublicRequestType.SHIP_TO,
            **address.model_dump(exclude_none=True),
        }
    ]


def parse_estimate_response(
    response: models.PageTransactionEstimateResponse
    | models.TransactionEstimateResponse,
) -> models.TransactionEstimateResponse:
    if isinstance(response, models.TransactionEstimateResponse):
        return response
    return response.items[0]


def build_payment_intent_metadata(
    external_id: str,
    estimate: models.TransactionEstimateResponse,
    subtotal_cents: int,
    tax_cents: int,
    sale_total_cents: int,
    processing_fee_cents: int = 0,
) -> dict[str, str]:
    items_json = json.dumps(
        [compact_estimate_item(item) for item in estimate.transaction_items],
        separators=(",", ":"),
    )

    metadata: dict[str, str] = {
        "kintsugi_external_id": external_id,
        "tax_rate": estimate.tax_rate_calculated or "",
        "taxable_amount": estimate.taxable_amount or "",
        "subtotal": str(subtotal_cents),
        "tax_amount": str(tax_cents),
        "total_amount": str(sale_total_cents),
        "processing_fee": str(processing_fee_cents),
    }
    metadata.update(
        encode_stripe_metadata_chunks(ESTIMATE_ITEMS_METADATA_KEY, items_json)
    )
    return metadata


async def estimate_tax(
    body: CreatePaymentIntentRequest,
    kintsugi: SDK,
) -> tuple[str, models.TransactionEstimateResponse]:
    now = datetime.now(UTC)
    external_id = f"est_{uuid4().hex}"
    customer = build_customer(body.customer)

    try:
        response = await kintsugi.tax_estimation.estimate_async(
            date_=now,
            external_id=external_id,
            currency=models.CurrencyEnum(body.currency.upper()),
            transaction_items=build_transaction_items(
                body.line_items, external_id, now
            ),
            addresses=build_addresses(body.shipping_address),
            customer=customer,
            marketplace=False,
        )
        estimate = parse_estimate_response(response)
    except errors.ResponseValidationError as exc:
        if exc.status_code != 200:
            raise
        estimate = models.TransactionEstimateResponse.model_validate(
            exc.raw_response.json()
        )

    logger.info(
        "Tax estimate complete",
        external_id=external_id,
        tax_amount=estimate.total_tax_amount_calculated,
    )
    return external_id, estimate
