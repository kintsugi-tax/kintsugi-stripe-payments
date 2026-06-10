from stripe import StripeClient

from schemas import LineItem

SUBSCRIPTION_STRIPE_PRODUCT_ID = "prod_UgD3LBtYfUIjrU"
SUBSCRIPTION_PRODUCT_NAME = "Kintsugi Monthly Subscription"


async def get_subscription_line_item(stripe_client: StripeClient) -> LineItem:
    product = await stripe_client.v1.products.retrieve_async(
        SUBSCRIPTION_STRIPE_PRODUCT_ID
    )
    default_price = product.default_price
    price_id = default_price if isinstance(default_price, str) else default_price.id
    price = await stripe_client.v1.prices.retrieve_async(price_id)

    if price.unit_amount is None:
        raise ValueError(f"Stripe price {price_id} has no unit_amount")

    return LineItem(
        external_product_id=product.id,
        product_name=product.name or SUBSCRIPTION_PRODUCT_NAME,
        amount=price.unit_amount / 100,
        quantity=1,
    )
