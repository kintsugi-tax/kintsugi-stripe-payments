import math

from config import settings


def processing_fee_cents(sale_total_cents: int) -> int:
    """Gross-up processing fee so the merchant nets the sale total after Stripe fees."""
    if not settings.stripe_pass_processing_fee_to_customer:
        return 0

    percent = settings.stripe_processing_fee_percent
    fixed = settings.stripe_processing_fee_fixed_cents
    gross = math.ceil((sale_total_cents + fixed) / (1 - percent))
    return gross - sale_total_cents
