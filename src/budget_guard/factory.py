from budget_guard.config import settings
from budget_guard.adapters.billing import MockBillingProvider, VolcBillingProvider
from budget_guard.adapters.limiter import LogLimiter, WebhookLimiter

def billing_provider():
    if settings.billing_provider == "mock":
        return MockBillingProvider()
    return VolcBillingProvider(settings.volc_access_key, settings.volc_secret_key, settings.volc_billing_endpoint, settings.volc_region)

def limiter_provider():
    if settings.limiter_provider == "log":
        return LogLimiter()
    return WebhookLimiter(settings.limiter_webhook_url, settings.limiter_webhook_token, settings.dry_run)
