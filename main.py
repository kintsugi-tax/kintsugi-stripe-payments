from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from stripe import StripeClient

from config import settings
from fees import processing_fee_cents
from handlers import handle_payment_intent_failed, handle_payment_intent_succeeded
from kintsugi_client import create_kintsugi_sdk
from logger import logger
from products import get_subscription_line_item
from schemas import (
    CreatePaymentIntentRequest,
    CreatePaymentIntentResponse,
    TaxBreakdown,
)
from tax import build_payment_intent_metadata, dollars_to_cents, estimate_tax


load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with create_kintsugi_sdk() as kintsugi:
        app.state.kintsugi = kintsugi
        yield


app = FastAPI(lifespan=lifespan)
client = StripeClient(api_key=settings.stripe_api_key)


@app.get("/api/config")
async def api_config():
    return {"publishable_key": settings.stripe_publishable_key}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    event = client.construct_event(
        payload,
        sig_header,
        settings.stripe_webhook_secret,
    )
    event_type, data = event.type, event.data.object
    match event_type:
        case "payment_intent.succeeded":
            await handle_payment_intent_succeeded(
                data, request.app.state.kintsugi, client
            )
        case "payment_intent.canceled" | "payment_intent.payment_failed":
            await handle_payment_intent_failed(data)
        case _:
            logger.info(f"Unknown event type: {event_type}")
    return {"message": "Webhook received"}


def stripe_product_code(external_product_id: str) -> str:
    return external_product_id.removeprefix("prod_")[:12]


def build_payment_intent_params(
    body: CreatePaymentIntentRequest,
    external_id: str,
    estimate,
) -> dict:
    subtotal_cents = sum(dollars_to_cents(item.amount) for item in body.line_items)
    tax_cents = dollars_to_cents(estimate.total_tax_amount_calculated or "0")
    sale_total_cents = subtotal_cents + tax_cents
    fee_cents = processing_fee_cents(sale_total_cents)
    charge_cents = sale_total_cents + fee_cents

    stripe_line_items = []
    for request_item, estimate_item in zip(
        body.line_items,
        estimate.transaction_items,
        strict=True,
    ):
        quantity = max(int(request_item.quantity), 1)
        unit_cost = dollars_to_cents(request_item.amount / request_item.quantity)
        line_tax_cents = dollars_to_cents(estimate_item.tax_amount or "0")
        product_name = (
            request_item.product_name
            or request_item.description
            or request_item.external_product_id
        )
        stripe_line_items.append(
            {
                "product_name": product_name,
                "product_code": stripe_product_code(request_item.external_product_id),
                "quantity": quantity,
                "unit_cost": unit_cost,
                "tax": {
                    "total_tax_amount": line_tax_cents,
                },
            }
        )

    if fee_cents:
        stripe_line_items.append(
            {
                "product_name": "Processing Fee",
                "product_code": "PROCESSING",
                "quantity": 1,
                "unit_cost": fee_cents,
                "tax": {"total_tax_amount": 0},
            }
        )

    params: dict = {
        "amount": charge_cents,
        "currency": body.currency.lower(),
        "automatic_payment_methods": {"enabled": True},
        "metadata": build_payment_intent_metadata(
            external_id,
            estimate,
            subtotal_cents,
            tax_cents,
            sale_total_cents,
            fee_cents,
        ),
        "amount_details": {"line_items": stripe_line_items},
    }

    if body.customer.email:
        params["receipt_email"] = body.customer.email

    if body.customer.name and body.shipping_address.street_1:
        params["shipping"] = {
            "name": body.customer.name,
            "address": {
                "line1": body.shipping_address.street_1,
                "line2": body.shipping_address.street_2,
                "city": body.shipping_address.city,
                "state": body.shipping_address.state,
                "postal_code": body.shipping_address.postal_code,
                "country": body.shipping_address.country,
            },
        }

    return params, sale_total_cents, fee_cents, charge_cents


async def resolve_payment_request(
    body: CreatePaymentIntentRequest,
) -> CreatePaymentIntentRequest:
    if body.line_items:
        return body

    line_item = await get_subscription_line_item(client)
    logger.info(
        "Using default subscription product",
        product_id=line_item.external_product_id,
        amount=line_item.amount,
    )
    return body.model_copy(update={"line_items": [line_item]})


@app.post("/stripe/payments")
async def stripe_payment(
    body: CreatePaymentIntentRequest,
    request: Request,
) -> CreatePaymentIntentResponse:
    body = await resolve_payment_request(body)
    external_id, estimate = await estimate_tax(body, request.app.state.kintsugi)
    params, sale_total_cents, fee_cents, charge_cents = build_payment_intent_params(
        body, external_id, estimate
    )

    payment_intent = await client.v1.payment_intents.create_async(params)
    if not payment_intent.client_secret:
        raise HTTPException(
            status_code=500, detail="Payment intent missing client secret"
        )

    subtotal_cents = int(params["metadata"]["subtotal"])
    tax_cents = int(params["metadata"]["tax_amount"])

    logger.info(
        "Payment intent created",
        payment_intent_id=payment_intent.id,
        external_id=external_id,
        tax_cents=tax_cents,
        processing_fee_cents=fee_cents,
        charge_cents=charge_cents,
    )

    return CreatePaymentIntentResponse(
        payment_intent_id=payment_intent.id,
        client_secret=payment_intent.client_secret,
        external_id=external_id,
        tax=TaxBreakdown(
            subtotal=subtotal_cents,
            tax_amount=tax_cents,
            sale_total=sale_total_cents,
            processing_fee=fee_cents,
            total_amount=charge_cents,
            tax_rate=estimate.tax_rate_calculated,
        ),
    )


app.mount("/", StaticFiles(directory="static", html=True), name="static")
