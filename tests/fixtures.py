from datetime import UTC, datetime

from kintsugi_tax_platform_sdk import models
from pytest import fixture

from schemas import Address, CustomerInfo, LineItem, ResolvedPaymentRequest


@fixture
def estimate() -> models.TransactionEstimateResponse:
    return models.TransactionEstimateResponse.model_validate(
        {
            "date": datetime(2026, 1, 1, tzinfo=UTC),
            "external_id": "est_1",
            "currency": "USD",
            "addresses": [],
            "total_tax_amount_calculated": "4.50",
            "taxable_amount": "75.00",
            "tax_rate_calculated": "0.06",
            "transaction_items": [
                {
                    "date": datetime(2026, 1, 1, tzinfo=UTC),
                    "amount": "75.00",
                    "external_product_id": "prod_abc",
                    "product_name": "Widget",
                    "quantity": "1",
                    "tax_amount": "4.50",
                    "taxable_amount": "75.00",
                    "tax_rate": "0.06",
                }
            ],
        }
    )


@fixture
def payment_request() -> ResolvedPaymentRequest:
    return ResolvedPaymentRequest(
        customer=CustomerInfo(name="Jane Doe", email="jane@example.com"),
        shipping_address=Address(
            street_1="123 Main St",
            street_2="Apt 4",
            city="Highgrove",
            state="KY",
            postal_code="40013",
        ),
        line_items=[
            LineItem(external_product_id="prod_abc", product_name="Widget", amount=75.0)
        ],
    )
