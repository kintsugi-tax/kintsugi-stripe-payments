import json
from datetime import UTC, datetime
from typing import Any

from kintsugi_tax_platform_sdk import SDK, errors, models
from stripe import StripeClient

from config import settings
from logger import get_logger
from tax import parse_estimate_items_metadata, parse_estimate_summary_metadata

log = get_logger(__name__)


def stripe_value(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def stripe_metadata_dict(obj: Any) -> dict[str, Any]:
    metadata = stripe_value(obj, "metadata")
    if not metadata:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if hasattr(metadata, "to_dict"):
        return metadata.to_dict()
    return {}


def parse_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def estimate_transaction_items(metadata: dict) -> list[dict]:
    return parse_estimate_items_metadata(metadata)


def apply_estimate_item_fields(
    item: dict,
    estimate_item: dict | None,
) -> dict:
    if not estimate_item:
        return item

    tax_amount = parse_optional_float(estimate_item.get("tax_amount"))
    tax_rate = parse_optional_float(estimate_item.get("tax_rate"))
    taxable_amount = parse_optional_float(estimate_item.get("taxable_amount"))

    if tax_amount is not None:
        item["tax_amount_imported"] = tax_amount
        item["tax_amount_calculated"] = tax_amount
    if tax_rate is not None:
        item["tax_rate_imported"] = tax_rate
    if taxable_amount is not None:
        item["taxable_amount"] = taxable_amount

    return item


def cents_to_dollars(cents: int) -> float:
    return cents / 100


def build_transaction_item(
    payment_intent,
    transaction_date: datetime,
    index: int,
    *,
    external_product_id: str,
    product_name: str | None,
    quantity: float | int,
    amount_cents: int,
    tax_cents: int,
    estimate_item: dict | None,
) -> dict:
    return apply_estimate_item_fields(
        {
            "organization_id": settings.kintsugi_organization_id,
            "date_": transaction_date,
            "external_id": f"{payment_intent.id}_{index}",
            "external_product_id": external_product_id,
            "product_name": product_name,
            "quantity": quantity,
            "amount": cents_to_dollars(amount_cents),
            "tax_amount_calculated": cents_to_dollars(tax_cents),
        },
        estimate_item,
    )


def build_addresses(payment_intent) -> list[dict]:
    shipping = stripe_value(payment_intent, "shipping")
    address = stripe_value(shipping, "address")
    if not address:
        return []

    return [
        {
            "type": models.AddressType.SHIP_TO,
            "street_1": stripe_value(address, "line1"),
            "street_2": stripe_value(address, "line2"),
            "city": stripe_value(address, "city"),
            "state": stripe_value(address, "state"),
            "postal_code": stripe_value(address, "postal_code"),
            "country": stripe_value(address, "country"),
        }
    ]


def stripe_line_items(payment_intent) -> list[Any]:
    amount_details = stripe_value(payment_intent, "amount_details")
    line_items = stripe_value(amount_details, "line_items")
    if not line_items:
        return []
    if isinstance(line_items, list):
        return line_items
    data = stripe_value(line_items, "data")
    if isinstance(data, list):
        return data
    return list(line_items)


def build_transaction_items_from_metadata(
    payment_intent,
    metadata: dict,
    transaction_date: datetime,
) -> list[dict]:
    estimate_items = estimate_transaction_items(metadata)
    if estimate_items:
        items: list[dict] = []
        for index, estimate_item in enumerate(estimate_items):
            amount = float(estimate_item.get("amount", 0))
            tax_cents = dollars_to_cents(estimate_item.get("tax_amount", "0"))
            items.append(
                build_transaction_item(
                    payment_intent,
                    transaction_date,
                    index,
                    external_product_id=estimate_item.get("external_product_id", ""),
                    product_name=estimate_item.get("product_name"),
                    quantity=estimate_item.get("quantity", 1),
                    amount_cents=dollars_to_cents(amount),
                    tax_cents=tax_cents,
                    estimate_item=estimate_item,
                )
            )
        return items

    snapshot = metadata.get("line_items_json")
    if not snapshot:
        return []

    try:
        line_items = json.loads(snapshot)
    except json.JSONDecodeError:
        log.warning(
            "metadata.invalid_line_items_json",
            payment_intent_id=payment_intent.id,
        )
        return []

    items = []
    for index, line_item in enumerate(line_items):
        amount_cents = int(line_item.get("amount_cents", 0))
        tax_cents = int(line_item.get("tax_cents", 0))
        items.append(
            build_transaction_item(
                payment_intent,
                transaction_date,
                index,
                external_product_id=line_item.get("external_product_id", ""),
                product_name=line_item.get("product_name"),
                quantity=line_item.get("quantity", 1),
                amount_cents=amount_cents,
                tax_cents=tax_cents,
                estimate_item=None,
            )
        )
    return items


def dollars_to_cents(amount: str | float) -> int:
    return int(round(float(amount) * 100))


def is_processing_fee_line(line_item) -> bool:
    return (
        stripe_value(line_item, "product_code") == "PROCESSING"
        or stripe_value(line_item, "product_name") == "Processing Fee"
    )


def build_transaction_items(
    payment_intent,
    metadata: dict,
    transaction_date: datetime,
) -> list[dict]:
    estimate_items = estimate_transaction_items(metadata)
    product_ids = [
        product_id.strip()
        for product_id in metadata.get("product_external_ids", "").split(",")
        if product_id.strip()
    ]
    line_items = [
        line_item
        for line_item in stripe_line_items(payment_intent)
        if not is_processing_fee_line(line_item)
    ]

    items: list[dict] = []
    for index, line_item in enumerate(line_items):
        quantity = stripe_value(line_item, "quantity", 1)
        unit_cost_cents = stripe_value(line_item, "unit_cost", 0)
        tax = stripe_value(line_item, "tax")
        tax_cents = stripe_value(tax, "total_tax_amount", 0)
        estimate_item = estimate_items[index] if index < len(estimate_items) else None
        product_id = (
            stripe_value(estimate_item, "external_product_id")
            if estimate_item
            else product_ids[index]
            if index < len(product_ids)
            else metadata.get("product_external_id", "")
        )

        items.append(
            build_transaction_item(
                payment_intent,
                transaction_date,
                index,
                external_product_id=product_id or "",
                product_name=stripe_value(line_item, "product_name"),
                quantity=quantity,
                amount_cents=unit_cost_cents * quantity,
                tax_cents=tax_cents,
                estimate_item=estimate_item,
            )
        )

    if items:
        return items

    return build_transaction_items_from_metadata(
        payment_intent, metadata, transaction_date
    )


async def fetch_payment_intent_for_sync(
    stripe_client: StripeClient,
    payment_intent_id: str,
):
    return await stripe_client.v1.payment_intents.retrieve_async(
        payment_intent_id,
        params={"expand": ["amount_details.line_items"]},
    )


async def sync_transaction_from_payment_intent(
    kintsugi: SDK,
    payment_intent,
) -> None:
    metadata = stripe_metadata_dict(payment_intent)
    estimate_id = metadata.get("kintsugi_external_id")
    if not estimate_id:
        log.warning(
            "kintsugi.sync.skipped",
            reason="missing_estimate_id",
            payment_intent_id=payment_intent.id,
        )
        return

    subtotal_cents = int(metadata.get("subtotal", 0))
    tax_cents = int(metadata.get("tax_amount", 0))
    sale_total_cents = int(metadata.get("total_amount", 0))
    if not sale_total_cents:
        processing_fee = int(metadata.get("processing_fee", 0))
        charge_cents = int(stripe_value(payment_intent, "amount", 0))
        sale_total_cents = (
            charge_cents - processing_fee if charge_cents else subtotal_cents + tax_cents
        )
    estimate_snapshot = parse_estimate_summary_metadata(metadata)
    tax_rate_imported = parse_optional_float(
        estimate_snapshot.get("tax_rate_calculated")
    )
    total_tax_amount_imported = parse_optional_float(
        estimate_snapshot.get("total_tax_amount_calculated")
    ) or cents_to_dollars(tax_cents)
    taxable_amount = parse_optional_float(estimate_snapshot.get("taxable_amount"))
    transaction_date = datetime.fromtimestamp(payment_intent.created, tz=UTC)
    shipping = stripe_value(payment_intent, "shipping") or {}

    customer = {
        "organization_id": settings.kintsugi_organization_id,
        "name": stripe_value(shipping, "name"),
        "email": stripe_value(payment_intent, "receipt_email"),
    }

    try:
        transaction = await kintsugi.transactions.create_async(
            organization_id=settings.kintsugi_organization_id,
            external_id=payment_intent.id,
            date_=transaction_date,
            addresses=build_addresses(payment_intent),
            transaction_items=build_transaction_items(
                payment_intent, metadata, transaction_date
            ),
            customer=customer,
            type_=models.TransactionTypeEnum.SALE,
            total_amount=cents_to_dollars(sale_total_cents),
            total_tax_amount_calculated=cents_to_dollars(tax_cents),
            total_tax_amount_imported=total_tax_amount_imported,
            tax_rate_imported=tax_rate_imported,
            taxable_amount=taxable_amount or cents_to_dollars(subtotal_cents),
            currency=models.CurrencyEnum(
                str(stripe_value(payment_intent, "currency", "usd")).upper()
            ),
            source=models.SourceEnum.STRIPE,
            secondary_external_id=estimate_id,
            description=f"Stripe payment {payment_intent.id}",
        )
    except errors.ResponseValidationError as exc:
        if exc.status_code == 202:
            transaction = models.TransactionRead.model_validate(exc.raw_response.json())
        else:
            log.error(
                "kintsugi.sync.failed",
                payment_intent_id=payment_intent.id,
                status_code=exc.status_code,
                detail=exc.body,
            )
            return
    except errors.SDKError as exc:
        log.error(
            "kintsugi.sync.failed",
            payment_intent_id=payment_intent.id,
            status_code=exc.status_code,
            detail=exc.body,
        )
        return

    log.info(
        "kintsugi.sync.completed",
        payment_intent_id=payment_intent.id,
        kintsugi_transaction_id=transaction.id,
        estimate_id=estimate_id,
    )
