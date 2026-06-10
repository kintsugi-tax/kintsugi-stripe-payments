from kintsugi_tax_platform_sdk import SDK
from stripe import StripeClient

from logger import logger
from transaction_sync import (
    fetch_payment_intent_for_sync,
    stripe_metadata_dict,
    sync_transaction_from_payment_intent,
)


async def handle_payment_intent_succeeded(
    payment_intent,
    kintsugi: SDK,
    stripe_client: StripeClient,
) -> None:
    payment_intent = await fetch_payment_intent_for_sync(
        stripe_client, payment_intent.id
    )
    logger.info(
        "Handling payment intent succeeded",
        payment_intent_id=payment_intent.id,
        amount=payment_intent.amount,
        metadata=stripe_metadata_dict(payment_intent),
    )
    await sync_transaction_from_payment_intent(kintsugi, payment_intent)


async def handle_payment_intent_failed(payment_intent) -> None:
    logger.info("Handling payment intent failed", payment_intent_id=payment_intent.id)
