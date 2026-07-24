from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "dev"
    app_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite:///./budget_guard.db"
    budget_config_path: Path = Path("./config/budgets.yaml")
    poll_interval_minutes: int = 60
    dry_run: bool = True
    billing_provider: str = "mock"
    volc_access_key: str = ""
    volc_secret_key: str = ""
    volc_billing_endpoint: str = "https://billing.volcengineapi.com"
    volc_region: str = "cn-north-1"
    limiter_provider: str = "webhook"
    limiter_webhook_url: str = "http://localhost:9000/internal/projects/{project_id}/access"
    limiter_webhook_token: str = ""
    alert_webhook_url: str = ""
    config_api_token: str = ""

settings = Settings()
