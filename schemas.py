from pydantic import BaseModel, Field


class Address(BaseModel):
    street_1: str | None = None
    street_2: str | None = None
    city: str | None = None
    state: str
    postal_code: str
    country: str = "US"


class CustomerInfo(BaseModel):
    external_id: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    street_1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class LineItem(BaseModel):
    external_product_id: str
    amount: float = Field(gt=0, description="Line subtotal in major currency units")
    quantity: float = Field(default=1.0, gt=0)
    description: str | None = None
    product_name: str | None = None


class CreatePaymentIntentRequest(BaseModel):
    currency: str = Field(default="usd", min_length=3, max_length=3)
    line_items: list[LineItem] | None = None
    customer: CustomerInfo
    shipping_address: Address


class TaxBreakdown(BaseModel):
    subtotal: int
    tax_amount: int
    sale_total: int
    processing_fee: int = 0
    total_amount: int
    tax_rate: str | None = None


class CreatePaymentIntentResponse(BaseModel):
    payment_intent_id: str
    client_secret: str
    external_id: str
    tax: TaxBreakdown


class CreateCheckoutSessionRequest(CreatePaymentIntentRequest):
    success_url: str | None = None
    cancel_url: str | None = None


class CreateCheckoutSessionResponse(BaseModel):
    session_id: str
    checkout_url: str
    external_id: str
    tax: TaxBreakdown
