from budget_guard.config import settings
from budget_guard.adapters.billing import VolcBillingProvider
from budget_guard.adapters.limiter import LogLimiter, VolcLimiter

def billing_provider():
    if settings.billing_provider != "volc":
        raise RuntimeError("BILLING_PROVIDER must be volc")
    return VolcBillingProvider(settings.volc_access_key, settings.volc_secret_key, settings.volc_billing_endpoint, settings.volc_region)

def limiter_provider():
    if settings.limiter_provider == "log":
        return LogLimiter()
    if settings.limiter_provider == "volc":
        return VolcLimiter(
            settings.volc_access_key, settings.volc_secret_key, settings.volc_region,
            settings.volc_ark_endpoint, settings.dry_run,
        )
    raise RuntimeError(f"unsupported LIMITER_PROVIDER: {settings.limiter_provider}")
