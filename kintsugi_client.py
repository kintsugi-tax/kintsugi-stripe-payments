from kintsugi_tax_platform_sdk import SDK, models

from config import settings


def create_kintsugi_sdk() -> SDK:
    return SDK(
        security=models.Security(
            api_key_header=settings.kintsugi_api_key,
            custom_header=settings.kintsugi_organization_id,
        ),
    )
