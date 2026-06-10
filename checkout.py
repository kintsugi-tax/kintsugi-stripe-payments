from schemas import CreatePaymentIntentRequest
from pricing import (
    build_kintsugi_metadata,
    compute_charge_totals,
    iter_product_lines,
    stripe_product_code,
)


def build_checkout_session_line_items(
    body: CreatePaymentIntentRequest,
    estimate,
    totals,
) -> list[dict]:
    currency = body.currency.lower()
    line_items: list[dict] = []

    for line in iter_product_lines(body, estimate):
        line_items.append(
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": line.unit_cost_cents,
                    "product_data": {
                        "name": line.product_name,
                        "metadata": {
                            "external_product_id": line.request_item.external_product_id,
                            "product_code": stripe_product_code(
                                line.request_item.external_product_id
                            ),
                        },
                    },
                },
                "quantity": line.quantity,
            }
        )

    if totals.tax_cents:
        line_items.append(
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": totals.tax_cents,
                    "product_data": {"name": "Sales Tax"},
                },
                "quantity": 1,
            }
        )

    if totals.fee_cents:
        line_items.append(
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": totals.fee_cents,
                    "product_data": {"name": "Processing Fee"},
                },
                "quantity": 1,
            }
        )

    return line_items


def build_checkout_session_params(
    body: CreatePaymentIntentRequest,
    external_id: str,
    estimate,
    *,
    success_url: str,
    cancel_url: str,
) -> dict:
    totals = compute_charge_totals(body, estimate)
    metadata = build_kintsugi_metadata(external_id, estimate, totals)
    metadata["checkout_flow"] = "hosted"

    params: dict = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": build_checkout_session_line_items(body, estimate, totals),
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
    }

    if body.customer.email:
        params["customer_email"] = body.customer.email

    if body.customer.name and body.shipping_address.street_1:
        params["payment_intent_data"]["shipping"] = {
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
