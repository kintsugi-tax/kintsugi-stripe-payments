from dataclasses import dataclass
from typing import NamedTuple

from kintsugi_tax_platform_sdk import models
from stripe.params import (
    PaymentIntentCreateParamsAmountDetailsLineItem,
    PaymentIntentCreateParamsShipping,
    PaymentIntentCreateParamsShippingAddress,
)

from fees import processing_fee_cents
from schemas import Address, LineItem, ResolvedPaymentRequest
from tax import build_payment_intent_metadata, dollars_to_cents


@dataclass(frozen=True)
class ChargeTotals:
    subtotal_cents: int
    tax_cents: int
    sale_total_cents: int
    fee_cents: int
    charge_cents: int


class PaymentParams[ParamsT](NamedTuple):
    params: ParamsT
    totals: ChargeTotals


@dataclass(frozen=True)
class ProductLineContext:
    request_item: LineItem
    estimate_item: object
    quantity: int
    unit_cost_cents: int
    line_tax_cents: int
    product_name: str


def stripe_product_code(external_product_id: str) -> str:
    return external_product_id.removeprefix("prod_")[:12]


def compute_charge_totals(
    body: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
) -> ChargeTotals:
    subtotal_cents = sum(dollars_to_cents(item.amount) for item in body.line_items)
    tax_cents = dollars_to_cents(estimate.total_tax_amount_calculated or "0")
    sale_total_cents = subtotal_cents + tax_cents
    fee_cents = processing_fee_cents(sale_total_cents)
    charge_cents = sale_total_cents + fee_cents
    return ChargeTotals(
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
        sale_total_cents=sale_total_cents,
        fee_cents=fee_cents,
        charge_cents=charge_cents,
    )


def build_kintsugi_metadata(
    external_id: str,
    estimate: models.TransactionEstimateResponse,
    totals: ChargeTotals,
) -> dict[str, str]:
    return build_payment_intent_metadata(
        external_id,
        estimate,
        totals.subtotal_cents,
        totals.tax_cents,
        totals.sale_total_cents,
        totals.fee_cents,
    )


def iter_product_lines(
    body: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
) -> list[ProductLineContext]:
    lines: list[ProductLineContext] = []
    for request_item, estimate_item in zip(
        body.line_items,
        estimate.transaction_items,
        strict=True,
    ):
        quantity = max(int(request_item.quantity), 1)
        lines.append(
            ProductLineContext(
                request_item=request_item,
                estimate_item=estimate_item,
                quantity=quantity,
                unit_cost_cents=dollars_to_cents(
                    request_item.amount / request_item.quantity
                ),
                line_tax_cents=dollars_to_cents(estimate_item.tax_amount or "0"),
                product_name=(
                    request_item.product_name
                    or request_item.description
                    or request_item.external_product_id
                ),
            )
        )
    return lines


def build_payment_intent_amount_details(
    body: ResolvedPaymentRequest,
    estimate: models.TransactionEstimateResponse,
    totals: ChargeTotals,
) -> list[PaymentIntentCreateParamsAmountDetailsLineItem]:
    line_items: list[PaymentIntentCreateParamsAmountDetailsLineItem] = [
        {
            "product_name": line.product_name,
            "product_code": stripe_product_code(line.request_item.external_product_id),
            "quantity": line.quantity,
            "unit_cost": line.unit_cost_cents,
            "tax": {"total_tax_amount": line.line_tax_cents},
        }
        for line in iter_product_lines(body, estimate)
    ]
    if totals.fee_cents:
        line_items.append(
            {
                "product_name": "Processing Fee",
                "product_code": "PROCESSING",
                "quantity": 1,
                "unit_cost": totals.fee_cents,
                "tax": {"total_tax_amount": 0},
            }
        )
    return line_items


def build_shipping_address(
    address: Address,
) -> PaymentIntentCreateParamsShippingAddress:
    """Build a Stripe address that omits the keys the request left empty.

    Stripe types these keys as `str` and treats a missing key as unset. An empty
    string would clear a stored value, so empty keys are left out.
    """
    shipping_address: PaymentIntentCreateParamsShippingAddress = {
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
    }
    if address.street_1:
        shipping_address["line1"] = address.street_1
    if address.street_2:
        shipping_address["line2"] = address.street_2
    if address.city:
        shipping_address["city"] = address.city
    return shipping_address


def build_shipping(
    body: ResolvedPaymentRequest,
) -> PaymentIntentCreateParamsShipping | None:
    # Stripe requires a recipient name and a street line to accept shipping.
    if not (body.customer.name and body.shipping_address.street_1):
        return None
    return {
        "name": body.customer.name,
        "address": build_shipping_address(body.shipping_address),
    }
