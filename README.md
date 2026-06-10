# Kintsugi Tax + Payments Integration Guide

A practical guide for collecting payments with correct sales tax using [Kintsugi](https://kintsugi.io) and a payment processor (Stripe in the examples below).

This repository is a **reference implementation** of the patterns described here. Use the guide to design your own integration; use the code to see one working version.

> **Prerequisite:** Connect your Stripe account in Kintsugi and sync products before implementing the payment flow below.

---

## The core idea

Kintsugi calculates tax. Your payment processor collects money. You connect the two in three phases:

| Phase | When | Kintsugi API | Payment processor |
|-------|------|--------------|-------------------|
| **1. Estimate** | Before checkout | `tax_estimation.estimate` | — |
| **2. Charge** | At checkout | — | Create payment for `subtotal + tax` |
| **3. Record** | After payment succeeds | `transactions.create` | Webhook confirms payment |

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Cart + address │────►│ Kintsugi estimate │────►│  Show tax total │
│  (ship-to)      │     │  est_*            │     │  to customer    │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                        ┌──────────────────┐              │
                        │ Charge customer  │◄─────────────┘
                        │ subtotal + tax   │
                        └────────┬─────────┘
                                 │ payment succeeded
                                 ▼
                        ┌──────────────────┐
                        │ Kintsugi         │
                        │ transaction      │
                        │ pi_* + est_*     │
                        └──────────────────┘
```

**Do not** use Stripe Tax (or another processor tax engine) alongside Kintsugi for the same sale — pick Kintsugi as the source of truth for rates and amounts, then pass those numbers into the payment.

---

## Before you start

Complete these steps **before** writing integration code. Tax estimates will not work correctly until your Stripe catalog is connected and synced in Kintsugi.

### Step 0: Connect Stripe and sync products

Kintsugi needs your Stripe products in its catalog so `external_product_id` on each line item (e.g. `prod_UgD3LBtYfUIjrU`) resolves to a tax classification.

1. **Create a Kintsugi organization** (if you do not have one) at [trykintsugi.com](https://trykintsugi.com).
2. **Connect Stripe** in the Kintsugi dashboard:
   - Go to **Data Sources** (or **Integrations**) for your organization.
   - Add **Stripe** and authorize the Stripe account you use for payments (test or live).
   - Confirm the connection shows as **active**.
3. **Sync products** — Kintsugi imports products from Stripe. After sync, each Stripe product ID appears in Kintsugi with the same `external_product_id` (`prod_…`).
4. **Review product taxability** — open the **Products** section in Kintsugi and confirm classifications look correct for anything you sell. Fix misclassified items before go-live.
5. **Note your credentials** for the API integration:
   - Kintsugi **API key**
   - **Organization ID** (`orgn_…`)
   - Stripe **secret key** and **publishable key** (from the same connected account)

You can also install the [Kintsugi app from the Stripe Marketplace](https://marketplace.stripe.com/apps/kintsugi---sales-tax-automation) as part of onboarding, but the API integration in this guide still requires products to exist in Kintsugi via the Stripe connection above.

> **Checklist before Phase 1:** Stripe connected ✓ · Products visible in Kintsugi ✓ · `prod_…` IDs match your checkout line items ✓ · API key + org ID ready ✓

### Kintsugi account setup

1. **Organization** — API key and organization ID (`orgn_…`) from the dashboard.
2. **Products** — synced from Stripe (Step 0). Do not rely on manual product entry unless you have a specific reason; keeping Stripe as the source keeps IDs aligned.
3. **Nexus & registrations** — Kintsugi uses your org configuration and the customer’s ship-to address to determine tax.

### What you need at checkout time

Collect these **before** calling the tax estimate:

- **Line items** — product ID, quantity, line subtotal (or unit price × quantity)
- **Ship-to address** — at minimum `state`, `postal_code`, and `country`; street and city improve accuracy
- **Customer** (optional but useful) — name, email

Tax is based on where the product is shipped, not where your business is located.

---

## Phase 1: Tax estimation

Call Kintsugi **on your server** after the customer provides their cart and address.

### Request shape

Each line item needs:

| Field | Notes |
|-------|-------|
| `external_id` | Unique per line on this estimate, e.g. `est_abc123_0` |
| `external_product_id` | Must match Kintsugi product catalog |
| `quantity` | Number of units |
| `amount` | Line subtotal in major currency units (e.g. dollars) |
| `date_` | Transaction date (usually `now`) |

Include a **ship-to address** on the estimate request. This drives jurisdiction and rate.

### Example (Python SDK)

```python
estimate = await kintsugi.tax_estimation.estimate_async(
    date_=datetime.now(UTC),
    external_id="est_abc123",           # unique per estimate
    currency="USD",
    transaction_items=[
        {
            "external_id": "est_abc123_0",
            "date_": datetime.now(UTC),
            "external_product_id": "prod_UgD3LBtYfUIjrU",
            "quantity": 1,
            "amount": 75.0,
        }
    ],
    addresses=[{
        "type": "SHIP_TO",
        "street_1": "123 Main St",
        "city": "Highgrove",
        "state": "KY",
        "postal_code": "40013",
        "country": "US",
    }],
    customer={"name": "Jane Doe", "email": "jane@example.com"},
)
```

### Response — what to use

| Field | Use for |
|-------|---------|
| `total_tax_amount_calculated` | Tax to add to the charge |
| `tax_rate_calculated` | Display / audit |
| `taxable_amount` | Subtotal Kintsugi taxed |
| `transaction_items[].tax_amount` | Per-line tax |
| `transaction_items[].tax_rate` | Per-line rate |

### Compute the charge

```
subtotal  = sum of line item amounts
tax       = estimate.total_tax_amount_calculated
sale_total = subtotal + tax
```

Show `subtotal`, `tax`, and `sale_total` to the customer **before** they pay. Store the estimate ID (`est_…`) — you will need it when recording the transaction.

---

## Phase 2: Collect payment

Charge exactly the **sale total** from Phase 1 (unless you deliberately add non-tax line items such as a processing fee — see below).

Two common Stripe patterns:

### Pattern A: Embedded checkout (Payment Element)

Payment UI lives on your site. You control the full experience.

1. Server: run Phase 1, create a **PaymentIntent** for `sale_total` (+ optional fees).
2. Return `client_secret` to the frontend.
3. Frontend: mount Stripe Payment Element, confirm payment.
4. Webhook: `payment_intent.succeeded` → Phase 3.

**When to use:** custom checkout, mobile apps, in-product payments.

**Stripe specifics:**

- Set `amount` on the PaymentIntent to the final charge in cents.
- Attach per-line tax via `amount_details.line_items[].tax` (Level 3 / reporting).
- Store estimate data in PaymentIntent **metadata** (see [Metadata contract](#metadata-contract)).
- Webhook payloads omit `amount_details` — re-fetch the PaymentIntent with `expand=["amount_details.line_items"]` before syncing.

### Pattern B: Hosted checkout (Stripe Checkout)

Stripe hosts the payment page. Less frontend work.

1. Server: run Phase 1, create a **Checkout Session** in `payment` mode.
2. Add line items: one per product, plus a separate **Sales Tax** line item, plus optional **Processing Fee** line.
3. Copy metadata onto `payment_intent_data.metadata`.
4. Redirect customer to `session.url`.
5. Webhook: `payment_intent.succeeded` → Phase 3 (Checkout creates a PaymentIntent under the hood).

**When to use:** fastest path to production, minimal payment UI code.

**Stripe specifics:**

- Tax is an explicit Checkout line item — not Stripe Tax.
- Use `payment_intent_data.metadata` so the webhook handler can sync without re-reading the session.

### Choosing a pattern

| | Embedded (Payment Element) | Hosted (Checkout) |
|--|---------------------------|-------------------|
| UI | Your site | Stripe-hosted |
| Stripe.js | Required | Optional |
| Tax on receipt | Your UI | Checkout line items |
| Effort | Higher | Lower |

Both patterns require the **ship-to address before Phase 1**. Do not create the payment first and calculate tax afterward.

### Optional: processing fee pass-through

Stripe charges its fee on the **full charge including tax**. If you want to net the full sale total after fees, gross up the charge:

```
fee = ceil((sale_total + fixed_cents) / (1 - percent)) - sale_total
charge = sale_total + fee
```

Report the fee as a separate non-tax line item. Kintsugi transaction sync should use **sale total** (subtotal + tax), not the gross charge — exclude the processing fee from taxable amounts.

---

## Phase 3: Record the transaction

When payment succeeds, create a Kintsugi **transaction** so the sale appears in compliance reporting.

### Link estimate to payment

| Kintsugi field | Value |
|----------------|-------|
| `external_id` | Your payment ID (e.g. Stripe `pi_…`) |
| `secondary_external_id` | Estimate ID from Phase 1 (`est_…`) |
| `source` | `STRIPE` (or your processor) |
| `type_` | `SALE` |

### Line items

For each product line, include:

- `external_product_id`, `quantity`, `amount`
- `tax_amount_imported` and `tax_rate_imported` from the estimate
- Ship-to address on the transaction

### Webhook flow

```
payment_intent.succeeded
  → read metadata (estimate ID, amounts, line-item snapshot)
  → re-fetch full PaymentIntent if line items are needed
  → transactions.create_async(...)
```

Handle failures gracefully — log and retry. Use the payment ID as the idempotency key so duplicate webhooks do not create duplicate transactions.

---

## Metadata contract

Payment processor metadata bridges Phase 1 and Phase 3. Stripe limits each value to **500 characters**, so keep payloads compact.

Store at payment creation time:

| Key | Content |
|-----|---------|
| `kintsugi_external_id` | Estimate ID (`est_…`) |
| `subtotal` | Subtotal in cents |
| `tax_amount` | Tax in cents |
| `total_amount` | Sale total in cents (subtotal + tax, **excluding** processing fee) |
| `tax_rate` | Estimate-level rate |
| `taxable_amount` | Taxable base from estimate |
| `processing_fee` | Processing fee in cents, if any |
| `estimate_items` | Compact JSON array of per-line tax/product data |
| `checkout_flow` | `embedded` or `hosted` (optional, for debugging) |

Compact line-item snapshot (fits Stripe limits):

```json
[{"p":"prod_…","n":"Product name","q":1,"a":75.0,"t":4.5,"r":0.06,"tb":75.0}]
```

Keys: `p` product ID, `n` name, `q` quantity, `a` amount, `t` tax, `r` rate, `tb` taxable amount.

If the JSON exceeds 500 characters, split across `estimate_items_0`, `estimate_items_1`, … with `estimate_items_parts`.

---

## Rules of thumb

0. **Connect Stripe first** — sync products in Kintsugi before calling the tax API.
1. **Estimate before charge** — never charge first and estimate later.
2. **One tax engine** — Kintsugi calculates; the processor only collects.
3. **Match product IDs** — use Stripe `prod_…` IDs that exist in Kintsugi after sync.
4. **Ship-to drives tax** — collect a full address when possible.
5. **Persist the estimate ID** — on the payment object, not only in your database.
6. **Sync on webhook** — do not rely on the client callback alone.
7. **Separate fee from sale** — processing fees are not part of the Kintsugi taxable sale.
8. **Re-fetch payment details** — webhook payloads are often incomplete.

---

## Reference implementation (this repo)

This project implements both Stripe patterns with the Kintsugi Python SDK.

### Run locally

After completing [Step 0: Connect Stripe and sync products](#step-0-connect-stripe-and-sync-products):

```bash
uv sync
cp .env.example .env   # fill in Stripe + Kintsugi keys
uv run uvicorn main:app --reload
```

Forward webhooks:

```bash
stripe listen --forward-to localhost:8000/stripe/webhook
```

Subscribe to `payment_intent.succeeded`.

### Demo pages

| URL | Pattern |
|-----|---------|
| `/` | Embedded — Payment Element |
| `/hosted.html` | Hosted — Stripe Checkout redirect |

### API endpoints

| Endpoint | Pattern |
|----------|---------|
| `POST /stripe/payments` | Phase 1 + 2 embedded → returns `client_secret` |
| `POST /stripe/checkout` | Phase 1 + 2 hosted → returns `checkout_url` |
| `POST /stripe/webhook` | Phase 3 on `payment_intent.succeeded` |

Request body (both payment endpoints):

```json
{
  "currency": "usd",
  "customer": { "name": "Jane Doe", "email": "jane@example.com" },
  "shipping_address": {
    "street_1": "123 Main St",
    "city": "Highgrove",
    "state": "KY",
    "postal_code": "40013",
    "country": "US"
  },
  "line_items": [{
    "external_product_id": "prod_UgD3LBtYfUIjrU",
    "product_name": "Kintsugi Monthly Subscription",
    "amount": 75.0,
    "quantity": 1
  }]
}
```

`amount` is the **line subtotal** in dollars. Hosted checkout accepts optional `success_url` and `cancel_url`.

### Code map

```
tax.py               Phase 1 — Kintsugi estimate
pricing.py           Charge totals + metadata
main.py              Embedded PaymentIntent route
checkout.py          Hosted Checkout Session route
transaction_sync.py  Phase 3 — Kintsugi transaction
handlers.py          Webhook → sync
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `STRIPE_API_KEY` | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | Publishable key (embedded flow) |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret |
| `KINTSUGI_API_KEY` | Kintsugi API key |
| `KINTSUGI_ORGANIZATION_ID` | Org ID (`orgn_…`) |
| `STRIPE_PASS_PROCESSING_FEE_TO_CUSTOMER` | Gross up charge for Stripe fees (default `true`) |
| `LOG_LEVEL` / `LOG_FORMAT` | Structured logging (`console` or `json`) |

---

## Adapting to other payment processors

The same three phases apply outside Stripe:

1. **Estimate** — unchanged; Kintsugi SDK/API.
2. **Charge** — create an order/payment for `sale_total`; attach metadata equivalent to the [metadata contract](#metadata-contract).
3. **Record** — on `payment.captured` / `payment.succeeded`, read metadata and call `transactions.create`.

Replace Stripe-specific fields (`PaymentIntent`, `amount_details`, Checkout Session line items) with your processor’s equivalents. Keep the estimate ID and amounts on the payment object so webhooks are self-contained.

---

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Kintsugi Intelligence, Inc.
