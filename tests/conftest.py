import os

# `config.settings` reads these at import time, so they must exist before any test
# module imports the app. Real environment values win over `.env`, so a developer's
# keys never reach a test.
os.environ.update(
    {
        "STRIPE_API_KEY": "sk_test_unit",
        "STRIPE_PUBLISHABLE_KEY": "pk_test_unit",
        "STRIPE_WEBHOOK_SECRET": "whsec_unit",
        "KINTSUGI_API_KEY": "kintsugi-unit",
        "KINTSUGI_ORGANIZATION_ID": "orgn_unit",
    }
)

pytest_plugins = ["tests.fixtures"]
