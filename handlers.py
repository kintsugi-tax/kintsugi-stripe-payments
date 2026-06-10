from logger import logger


async def handle_payment_intent_succeeded(payment_intent) -> None:
    logger.info(
        "Handling payment intent succeeded", payment_intent_id=payment_intent.id
    )


async def handle_payment_intent_failed(payment_intent) -> None:
    logger.info("Handling payment intent failed", payment_intent_id=payment_intent.id)
