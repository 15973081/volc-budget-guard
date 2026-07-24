import json
from pathlib import Path
from secrets import compare_digest
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
import yaml
from budget_guard.config import settings
from budget_guard.db import init_db, SessionLocal, EnforcementEvent
from budget_guard.factory import billing_provider, limiter_provider
from budget_guard.services.budgets import read_budget_config, save_budget_config
from budget_guard.services.poller import BillingPoller

app = FastAPI(title="Budget Guard", version="0.1.0")

@app.on_event("startup")
def startup(): init_db()

@app.get("/health")
def health(): return {"status": "ok"}

def require_config_token(authorization: str = "") -> None:
    scheme, _, token = authorization.partition(" ")
    if not settings.config_api_token:
        raise HTTPException(503, "CONFIG_API_TOKEN is not configured")
    if scheme.lower() != "bearer" or not compare_digest(token, settings.config_api_token):
        raise HTTPException(401, "invalid configuration token")

@app.get("/admin", include_in_schema=False)
def admin():
    return FileResponse(Path(__file__).with_name("admin.html"))

@app.get("/api/config")
def get_config(authorization: str = Header(default="")):
    require_config_token(authorization)
    return read_budget_config(settings.budget_config_path)

@app.put("/api/config")
def put_config(data: dict = Body(...), authorization: str = Header(default="")):
    require_config_token(authorization)
    try:
        save_budget_config(settings.budget_config_path, data)
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "saved"}

@app.post("/poll")
def poll(billing_cycle: str | None = None):
    try:
        return BillingPoller(billing_provider(), limiter_provider()).run_once(billing_cycle)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

@app.get("/events")
def events(limit: int = 100):
    with SessionLocal() as db:
        rows = db.scalars(select(EnforcementEvent).order_by(EnforcementEvent.id.desc()).limit(limit)).all()
        result = []
        for row in rows:
            budget_type, separator, window_key = row.billing_cycle.partition(":")
            try:
                detail = json.loads(row.detail)
            except (json.JSONDecodeError, TypeError):
                detail = {}
            result.append({
                "subsidiary_id": detail.get("subsidiary_id"),
                "company_name": detail.get("company_name"),
                "volc_project": detail.get("volc_project", row.project_id),
                "budget_type": budget_type if separator else "monthly",
                "window_key": window_key if separator else budget_type,
                "state": row.state,
                "amount": str(row.amount),
                "budget": str(row.budget),
                "created_at": row.created_at,
            })
        return result
