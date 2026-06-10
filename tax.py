from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from kintsugi_tax_platform_sdk import SDK, errors, models

from logger import logger
from schemas import Address, CreatePaymentIntentRequest, CustomerInfo, LineItem


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
