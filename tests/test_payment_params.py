from kintsugi_tax_platform_sdk import models
from pydantic import ValidationError
from pytest import mark, raises

from checkout import build_checkout_session_params
from main import build_payment_intent_params
from pricing import build_shipping_address
from schemas import Address, CustomerInfo, ResolvedPaymentRequest


def test_payment_intent_params_are_built_from_estimate(
    payment_request: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
) -> None:
    params, totals = build_payment_intent_params(payment_request, "est_1", estimate)

    assert totals.charge_cents == 8219
    assert params["amount"] == 8219
    assert params["currency"] == "usd"
    assert params["receipt_email"] == "jane@example.com"
    assert params["automatic_payment_methods"] == {"enabled": True}
    assert params["metadata"]["checkout_flow"] == "embedded"
    assert params["metadata"]["kintsugi_external_id"] == "est_1"
    assert params["amount_details"]["line_items"] == [
        {
            "product_name": "Widget",
            "product_code": "abc",
            "quantity": 1,
            "unit_cost": 7500,
            "tax": {"total_tax_amount": 450},
        },
        {
            "product_name": "Processing Fee",
            "product_code": "PROCESSING",
            "quantity": 1,
            "unit_cost": 269,
            "tax": {"total_tax_amount": 0},
        },
    ]
    assert params["shipping"] == {
        "name": "Jane Doe",
        "address": {
            "line1": "123 Main St",
            "line2": "Apt 4",
            "city": "Highgrove",
            "state": "KY",
            "postal_code": "40013",
            "country": "US",
        },
    }


@mark.parametrize(
    ("street_2", "city", "expected"),
    [
        (
            "Apt 4",
            "Highgrove",
            {
                "line1": "123 Main St",
                "line2": "Apt 4",
                "city": "Highgrove",
                "state": "KY",
                "postal_code": "40013",
                "country": "US",
            },
        ),
        (
            None,
            "Highgrove",
            {
                "line1": "123 Main St",
                "city": "Highgrove",
                "state": "KY",
                "postal_code": "40013",
                "country": "US",
            },
        ),
        (
            "Apt 4",
            None,
            {
                "line1": "123 Main St",
                "line2": "Apt 4",
                "state": "KY",
                "postal_code": "40013",
                "country": "US",
            },
        ),
        (
            None,
            None,
            {
                "line1": "123 Main St",
                "state": "KY",
                "postal_code": "40013",
                "country": "US",
            },
        ),
    ],
    ids=["all", "no-line2", "no-city", "neither"],
)
def test_shipping_address_omits_empty_keys(
    street_2: str | None, city: str | None, expected: dict[str, str]
) -> None:
    address = Address(
        street_1="123 Main St",
        street_2=street_2,
        city=city,
        state="KY",
        postal_code="40013",
    )

    assert build_shipping_address(address) == expected


@mark.parametrize(
    ("name", "street_1"),
    [(None, "123 Main St"), ("Jane Doe", None)],
    ids=["no-name", "no-street"],
)
def test_shipping_is_left_out_without_name_or_street(
    payment_request: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
    name: str | None,
    street_1: str | None,
) -> None:
    payment_request.customer = CustomerInfo(name=name)
    payment_request.shipping_address.street_1 = street_1

    payment_intent, _ = build_payment_intent_params(payment_request, "est_1", estimate)
    checkout, _ = build_checkout_session_params(
        payment_request,
        "est_1",
        estimate,
        success_url="https://app.test/ok",
        cancel_url="https://app.test/no",
    )

    assert "shipping" not in payment_intent
    assert "shipping" not in checkout["payment_intent_data"]


def test_checkout_session_params_are_built_from_estimate(
    payment_request: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
) -> None:
    params, totals = build_checkout_session_params(
        payment_request,
        "est_1",
        estimate,
        success_url="https://app.test/ok",
        cancel_url="https://app.test/no",
    )

    assert totals.charge_cents == 8219
    assert params["mode"] == "payment"
    assert params["success_url"] == "https://app.test/ok"
    assert params["cancel_url"] == "https://app.test/no"
    assert params["customer_email"] == "jane@example.com"
    assert params["metadata"]["checkout_flow"] == "hosted"
    assert params["payment_intent_data"]["metadata"] == params["metadata"]
    assert params["payment_intent_data"]["shipping"]["address"] == {
        "line1": "123 Main St",
        "line2": "Apt 4",
        "city": "Highgrove",
        "state": "KY",
        "postal_code": "40013",
        "country": "US",
    }
    assert [
        (item["price_data"]["product_data"]["name"], item["price_data"]["unit_amount"])
        for item in params["line_items"]
    ] == [("Widget", 7500), ("Sales Tax", 450), ("Processing Fee", 269)]


@mark.parametrize("line_items", [[], None], ids=["empty", "missing"])
def test_resolved_request_requires_line_items(
    line_items: list[object] | None,
) -> None:
    fields: dict[str, object] = {
        "customer": CustomerInfo(),
        "shipping_address": Address(state="KY", postal_code="40013"),
    }
    if line_items is not None:
        fields["line_items"] = line_items

    with raises(ValidationError):
        ResolvedPaymentRequest.model_validate(fields)
