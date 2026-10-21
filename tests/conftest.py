import os

# `config.settings` reads these at import time, so they must exist before any test
# module imports the app. Real environment values win over `.env`, so a developer's
# keys and fee settings never change a test result.
os.environ.update(
    {
        "STRIPE_API_KEY": "sk_test_unit",
        "STRIPE_PUBLISHABLE_KEY": "pk_test_unit",
        "STRIPE_WEBHOOK_SECRET": "whsec_unit",
        "KINTSUGI_API_KEY": "kintsugi-unit",
        "KINTSUGI_ORGANIZATION_ID": "orgn_unit",
        "STRIPE_PASS_PROCESSING_FEE_TO_CUSTOMER": "true",
        "STRIPE_PROCESSING_FEE_PERCENT": "0.029",
        "STRIPE_PROCESSING_FEE_FIXED_CENTS": "30",
        "ALLOWED_REDIRECT_ORIGINS": '["https://shop.test"]',
    }
)

pytest_plugins = ["tests.fixtures"]
