from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from stripe import StripeClient
from structlog.contextvars import bind_contextvars, unbind_contextvars

from checkout import build_checkout_session_params
from config import settings
from handlers import handle_payment_intent_failed, handle_payment_intent_succeeded
from kintsugi_client import create_kintsugi_sdk
from logger import configure_logging, get_logger
from logging_middleware import RequestLoggingMiddleware
from pricing import (
    build_kintsugi_metadata,
    build_payment_intent_amount_details,
    compute_charge_totals,
)
from products import get_subscription_line_item
from schemas import (
    CreateCheckoutSessionRequest,
    CreateCheckoutSessionResponse,
    CreatePaymentIntentRequest,
    CreatePaymentIntentResponse,
    TaxBreakdown,
)
from tax import estimate_tax


load_dotenv()
configure_logging(log_level=settings.log_level, log_format=settings.log_format)

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.started")
    async with create_kintsugi_sdk() as kintsugi:
        app.state.kintsugi = kintsugi
        yield
    log.info("app.stopped")


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
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
    bind_contextvars(
        stripe_event_id=event.id,
        stripe_event_type=event_type,
    )
    log.info("webhook.received")

    try:
        match event_type:
            case "payment_intent.succeeded":
                await handle_payment_intent_succeeded(
                    data, request.app.state.kintsugi, client
                )
            case "payment_intent.canceled" | "payment_intent.payment_failed":
                await handle_payment_intent_failed(data)
            case _:
                log.info("webhook.unhandled_event")
    finally:
        unbind_contextvars("stripe_event_id", "stripe_event_type")

    return {"message": "Webhook received"}


def build_payment_intent_params(
    body: CreatePaymentIntentRequest,
    external_id: str,
    estimate,
) -> dict:
    totals = compute_charge_totals(body, estimate)
    metadata = build_kintsugi_metadata(external_id, estimate, totals)
    metadata["checkout_flow"] = "embedded"

    params: dict = {
        "amount": totals.charge_cents,
        "currency": body.currency.lower(),
        "automatic_payment_methods": {"enabled": True},
        "metadata": metadata,
        "amount_details": {
            "line_items": build_payment_intent_amount_details(body, estimate, totals)
        },
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

    return params, totals


async def resolve_payment_request(
    body: CreatePaymentIntentRequest,
) -> CreatePaymentIntentRequest:
    if body.line_items:
        return body

    line_item = await get_subscription_line_item(client)
    log.info(
        "payment.default_product_used",
        product_id=line_item.external_product_id,
        amount=line_item.amount,
    )
    return body.model_copy(update={"line_items": [line_item]})


def tax_breakdown_from_totals(totals, tax_rate: str | None) -> TaxBreakdown:
    return TaxBreakdown(
        subtotal=totals.subtotal_cents,
        tax_amount=totals.tax_cents,
        sale_total=totals.sale_total_cents,
        processing_fee=totals.fee_cents,
        total_amount=totals.charge_cents,
        tax_rate=tax_rate,
    )


@app.post("/stripe/payments")
async def stripe_payment(
    body: CreatePaymentIntentRequest,
    request: Request,
) -> CreatePaymentIntentResponse:
    body = await resolve_payment_request(body)
    external_id, estimate = await estimate_tax(body, request.app.state.kintsugi)
    params, totals = build_payment_intent_params(body, external_id, estimate)

    payment_intent = await client.v1.payment_intents.create_async(params)
    if not payment_intent.client_secret:
        raise HTTPException(
            status_code=500, detail="Payment intent missing client secret"
        )

    log.info(
        "payment_intent.created",
        checkout_flow="embedded",
        payment_intent_id=payment_intent.id,
        estimate_id=external_id,
        subtotal_cents=totals.subtotal_cents,
        tax_cents=totals.tax_cents,
        processing_fee_cents=totals.fee_cents,
        charge_cents=totals.charge_cents,
    )

    return CreatePaymentIntentResponse(
        payment_intent_id=payment_intent.id,
        client_secret=payment_intent.client_secret,
        external_id=external_id,
        tax=tax_breakdown_from_totals(totals, estimate.tax_rate_calculated),
    )


@app.post("/stripe/checkout")
async def stripe_checkout(
    body: CreateCheckoutSessionRequest,
    request: Request,
) -> CreateCheckoutSessionResponse:
    body = await resolve_payment_request(body)
    external_id, estimate = await estimate_tax(body, request.app.state.kintsugi)

    success_url = body.success_url or (
        f"{str(request.base_url).rstrip('/')}/complete.html"
        "?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = body.cancel_url or f"{str(request.base_url).rstrip('/')}/hosted.html"

    params, totals = build_checkout_session_params(
        body,
        external_id,
        estimate,
        success_url=success_url,
        cancel_url=cancel_url,
    )

    session = await client.v1.checkout.sessions.create_async(params)
    if not session.url:
        raise HTTPException(status_code=500, detail="Checkout session missing URL")

    log.info(
        "checkout_session.created",
        checkout_flow="hosted",
        session_id=session.id,
        estimate_id=external_id,
        subtotal_cents=totals.subtotal_cents,
        tax_cents=totals.tax_cents,
        processing_fee_cents=totals.fee_cents,
        charge_cents=totals.charge_cents,
    )

    return CreateCheckoutSessionResponse(
        session_id=session.id,
        checkout_url=session.url,
        external_id=external_id,
        tax=tax_breakdown_from_totals(totals, estimate.tax_rate_calculated),
    )


app.mount("/", StaticFiles(directory="static", html=True), name="static")
