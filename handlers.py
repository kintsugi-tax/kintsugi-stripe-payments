from kintsugi_tax_platform_sdk import SDK
from stripe import StripeClient
from structlog.contextvars import bind_contextvars

from logger import get_logger
from transaction_sync import (
    fetch_payment_intent_for_sync,
    stripe_metadata_dict,
    sync_transaction_from_payment_intent,
)

log = get_logger(__name__)


async def handle_payment_intent_succeeded(
    payment_intent,
    kintsugi: SDK,
    stripe_client: StripeClient,
) -> None:
    bind_contextvars(payment_intent_id=payment_intent.id)
    payment_intent = await fetch_payment_intent_for_sync(
        stripe_client, payment_intent.id
    )
    metadata = stripe_metadata_dict(payment_intent)
    log.info(
        "payment_intent.succeeded",
        amount_cents=payment_intent.amount,
        estimate_id=metadata.get("kintsugi_external_id"),
        checkout_flow=metadata.get("checkout_flow"),
    )
    await sync_transaction_from_payment_intent(kintsugi, payment_intent)


async def handle_payment_intent_failed(payment_intent) -> None:
    bind_contextvars(payment_intent_id=payment_intent.id)
    log.info("payment_intent.failed")
