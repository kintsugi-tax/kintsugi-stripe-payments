const PRODUCTS = [
  {
    id: "prod_UgD3LBtYfUIjrU",
    name: "Kintsugi Monthly Subscription",
    amount: 75.0,
  },
];

const ADDRESS_PRESETS = {
  ky: {
    street: "123 Main St",
    city: "Highgrove",
    state: "KY",
    postalCode: "40013",
    country: "US",
  },
  ca: {
    street: "123 Main St",
    city: "San Francisco",
    state: "CA",
    postalCode: "94105",
    country: "US",
  },
};

const productSelect = document.getElementById("product");
const quantityInput = document.getElementById("quantity");
const productPrice = document.getElementById("product-price");
const addressPreset = document.getElementById("address-preset");
const summary = document.getElementById("summary");
const continueBtn = document.getElementById("continue");
const paymentSection = document.getElementById("payment-section");
const paymentForm = document.getElementById("payment-form");
const message = document.getElementById("message");
const submitBtn = document.getElementById("submit");

let stripe;
let elements;

function formatMoney(cents) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

function populateProducts() {
  for (const product of PRODUCTS) {
    const option = document.createElement("option");
    option.value = product.id;
    option.textContent = `${product.name} — $${product.amount.toFixed(2)}`;
    productSelect.appendChild(option);
  }
  updateProductPrice();
}

function selectedProduct() {
  return PRODUCTS.find((product) => product.id === productSelect.value);
}

function selectedQuantity() {
  const quantity = Number.parseInt(quantityInput.value, 10);
  return Number.isFinite(quantity) && quantity > 0 ? quantity : 1;
}

function updateProductPrice() {
  const product = selectedProduct();
  if (!product) {
    productPrice.textContent = "";
    return;
  }

  const quantity = selectedQuantity();
  const lineTotal = product.amount * quantity;
  productPrice.textContent =
    quantity === 1
      ? `$${product.amount.toFixed(2)} / month before tax`
      : `${quantity} × $${product.amount.toFixed(2)} = $${lineTotal.toFixed(2)} before tax`;
}

function applyAddressPreset() {
  const preset = ADDRESS_PRESETS[addressPreset.value];
  if (!preset) {
    return;
  }
  document.getElementById("street").value = preset.street;
  document.getElementById("city").value = preset.city;
  document.getElementById("state").value = preset.state;
  document.getElementById("postal-code").value = preset.postalCode;
  document.getElementById("country").value = preset.country;
}

function buildPaymentRequest() {
  const product = selectedProduct();
  const quantity = selectedQuantity();
  return {
    customer: {
      name: document.getElementById("name").value.trim(),
      email: document.getElementById("email").value.trim(),
    },
    shipping_address: {
      street_1: document.getElementById("street").value.trim(),
      city: document.getElementById("city").value.trim(),
      state: document.getElementById("state").value.trim().toUpperCase(),
      postal_code: document.getElementById("postal-code").value.trim(),
      country: document.getElementById("country").value.trim().toUpperCase(),
    },
    line_items: [
      {
        external_product_id: product.id,
        product_name: product.name,
        amount: product.amount * quantity,
        quantity,
      },
    ],
  };
}

async function loadStripe() {
  const configRes = await fetch("/api/config");
  if (!configRes.ok) {
    throw new Error("Could not load Stripe configuration.");
  }
  const config = await configRes.json();
  stripe = Stripe(config.publishable_key);
}

async function continueToPayment() {
  message.textContent = "";
  continueBtn.disabled = true;
  summary.textContent = "Calculating tax…";

  try {
    const res = await fetch("/stripe/payments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPaymentRequest()),
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error(err);
    }

    const data = await res.json();
    let summaryHtml =
      `<strong>Subtotal:</strong> ${formatMoney(data.tax.subtotal)}<br>` +
      `<strong>Tax:</strong> ${formatMoney(data.tax.tax_amount)}<br>`;
    if (data.tax.processing_fee > 0) {
      summaryHtml +=
        `<strong>Processing fee:</strong> ${formatMoney(data.tax.processing_fee)}<br>`;
    }
    summaryHtml += `<strong>Total:</strong> ${formatMoney(data.tax.total_amount)}`;
    summary.innerHTML = summaryHtml;

    elements = stripe.elements({ clientSecret: data.client_secret });
    elements.create("payment").mount("#payment-element");
    paymentSection.classList.remove("hidden");
    continueBtn.classList.add("hidden");
  } catch (error) {
    summary.textContent = "Could not start checkout.";
    message.textContent = error.message;
    continueBtn.disabled = false;
  }
}

paymentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  message.textContent = "";

  const { error } = await stripe.confirmPayment({
    elements,
    confirmParams: {
      return_url: `${window.location.origin}/complete.html`,
    },
  });

  if (error) {
    message.textContent = error.message;
    submitBtn.disabled = false;
  }
});

productSelect.addEventListener("change", updateProductPrice);
quantityInput.addEventListener("input", updateProductPrice);
addressPreset.addEventListener("change", applyAddressPreset);
continueBtn.addEventListener("click", continueToPayment);

populateProducts();
loadStripe().catch((error) => {
  message.textContent = error.message;
});
