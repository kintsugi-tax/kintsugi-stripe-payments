import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from kintsugi_tax_platform_sdk import models
from pytest import mark

import main
from handlers import handle_payment_intent_succeeded
from schemas import LineItem

REQUEST_BODY = {
    "customer": {"name": "Jane Doe"},
    "shipping_address": {"state": "KY", "postal_code": "40013"},
    "line_items": [{"external_product_id": "prod_abc", "amount": 75.0}],
}


@mark.parametrize(
    "override",
    [
        {},
        {
            "success_url": "https://shop.test/done",
            "cancel_url": "https://shop.test/back",
        },
    ],
    ids=["defaults", "configured-origin"],
)
@patch("main.estimate_tax", new_callable=AsyncMock)
@patch("main.client")
def test_checkout_accepts_configured_redirect_origin(
    stripe_client: MagicMock,
    estimate_tax: AsyncMock,
    estimate: models.TransactionEstimateResponse,
    override: dict[str, str],
) -> None:
    estimate_tax.return_value = ("est_1", estimate)
    create = AsyncMock(
        return_value=SimpleNamespace(id="cs_1", url="https://stripe.test/pay")
    )
    stripe_client.v1.checkout.sessions.create_async = create
    expected = {
        "success_url": "https://shop.test/complete.html?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": "https://shop.test/hosted.html",
        **override,
    }

    with TestClient(main.app) as http:
        response = http.post("/stripe/checkout", json={**REQUEST_BODY, **override})

    assert response.status_code == 200
    sent = create.call_args_list[0].args[0]
    assert (sent["success_url"], sent["cancel_url"]) == (
        expected["success_url"],
        expected["cancel_url"],
    )


@mark.parametrize(
    ("override", "headers"),
    [
        ({"success_url": "https://evil.test/phish"}, {}),
        ({"cancel_url": "https://evil.test/phish"}, {}),
        ({"success_url": "http://evil.test/phish"}, {"Host": "evil.test"}),
        ({"cancel_url": "http://evil.test/phish"}, {"Host": "evil.test"}),
    ],
    ids=[
        "foreign-success",
        "foreign-cancel",
        "spoofed-host-success",
        "spoofed-host-cancel",
    ],
)
@patch("main.estimate_tax", new_callable=AsyncMock)
@patch("main.client")
def test_checkout_rejects_foreign_redirect_origin(
    stripe_client: MagicMock,
    estimate_tax: AsyncMock,
    estimate: models.TransactionEstimateResponse,
    override: dict[str, str],
    headers: dict[str, str],
) -> None:
    estimate_tax.return_value = ("est_1", estimate)
    create = AsyncMock()
    stripe_client.v1.checkout.sessions.create_async = create

    with TestClient(main.app) as http:
        response = http.post(
            "/stripe/checkout", json={**REQUEST_BODY, **override}, headers=headers
        )

    assert response.status_code == 400
    create.assert_not_awaited()


def test_webhook_accepts_correctly_signed_event() -> None:
    payload = json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": "customer.created",
            "data": {"object": {}},
        }
    )
    timestamp = int(time.time())
    signature = hmac.new(
        b"whsec_unit", f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()

    with TestClient(main.app) as http:
        response = http.post(
            "/stripe/webhook",
            content=payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={signature}"},
        )

    assert response.status_code == 200


@mark.parametrize(
    "headers",
    [{}, {"Stripe-Signature": "t=1,v1=bad"}],
    ids=["missing", "invalid"],
)
def test_webhook_rejects_bad_signature_with_400(headers: dict[str, str]) -> None:
    with TestClient(main.app) as http:
        response = http.post("/stripe/webhook", content=b"{}", headers=headers)

    assert response.status_code == 400


@mark.parametrize(
    ("status", "expect_sync"),
    [("succeeded", True), ("requires_payment_method", False), ("canceled", False)],
)
@mark.anyio
@patch("handlers.sync_transaction_from_payment_intent", new_callable=AsyncMock)
@patch("handlers.fetch_payment_intent_for_sync", new_callable=AsyncMock)
async def test_sync_runs_only_for_succeeded_payment_intents(
    fetch: AsyncMock, sync: AsyncMock, status: str, expect_sync: bool
) -> None:
    fetch.return_value = SimpleNamespace(
        id="pi_1", status=status, amount=100, metadata={}
    )

    await handle_payment_intent_succeeded(
        SimpleNamespace(id="pi_1"), MagicMock(), MagicMock()
    )

    assert sync.await_count == int(expect_sync)


@mark.anyio
@patch("main.get_subscription_line_item", new_callable=AsyncMock)
async def test_default_line_item_fills_a_request_without_line_items(
    get_line_item: AsyncMock,
) -> None:
    get_line_item.return_value = LineItem(external_product_id="prod_sub", amount=75.0)
    body = main.CreatePaymentIntentRequest.model_validate(
        {k: v for k, v in REQUEST_BODY.items() if k != "line_items"}
    )

    resolved = await main.resolve_payment_request(body)

    assert [item.external_product_id for item in resolved.line_items] == ["prod_sub"]
