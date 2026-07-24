import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from secrets import compare_digest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
import yaml
from budget_guard.config import settings
from budget_guard.db import init_db, SessionLocal, BillDetail, EnforcementEvent
from budget_guard.factory import billing_provider, limiter_provider
from budget_guard.services.budgets import read_budget_config, save_budget_config
from budget_guard.services.poller import BillingPoller

app = FastAPI(title="Budget Guard", version="0.1.0")
scheduler = BackgroundScheduler()

@app.on_event("startup")
def startup():
    init_db()
    if not scheduler.running:
        scheduler.add_job(
            run_poll, "interval", minutes=settings.poll_interval_minutes,
            id="billing-poll", replace_existing=True, max_instances=1, coalesce=True,
            next_run_time=datetime.now(),
        )
        scheduler.start()

@app.on_event("shutdown")
def shutdown():
    if scheduler.running:
        scheduler.shutdown()

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

def validate_cycle(billing_cycle: str | None) -> str:
    cycle = billing_cycle or datetime.now().strftime("%Y-%m")
    try:
        if datetime.strptime(cycle, "%Y-%m").strftime("%Y-%m") != cycle:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(400, "billing_cycle must use YYYY-MM") from exc
    return cycle

def run_poll(billing_cycle: str | None = None):
    return BillingPoller(billing_provider(), limiter_provider()).run_once(billing_cycle)

def bill_snapshot(billing_cycle: str, limit: int = 500):
    totals = defaultdict(lambda: Decimal("0"))
    projects = defaultdict(lambda: Decimal("0"))
    items = []
    with SessionLocal() as db:
        rows = db.scalars(
            select(BillDetail)
            .where(BillDetail.billing_cycle == billing_cycle)
            .order_by(BillDetail.updated_at.desc(), BillDetail.id.desc())
        ).all()
    for row in rows:
        try:
            raw = json.loads(row.raw_json)
        except (json.JSONDecodeError, TypeError):
            raw = {}
        currency = str(raw.get("Currency") or "").upper() or "UNKNOWN"
        amount = Decimal(str(row.amount))
        totals[currency] += amount
        if row.project_id not in ("", "-", "UNASSIGNED"):
            projects[row.project_id, currency] += amount
        if len(items) < limit:
            items.append({
                "project": row.project_id,
                "product": row.product,
                "resource_id": row.resource_id,
                "amount": str(amount),
                "currency": currency,
                "updated_at": row.updated_at,
            })
    return {
        "billing_cycle": billing_cycle,
        "count": len(rows),
        "totals": {currency: str(amount) for currency, amount in totals.items()},
        "projects": [
            {"project": project, "currency": currency, "amount": str(amount)}
            for (project, currency), amount in sorted(projects.items())
        ],
        "items": items,
        "updated_at": max((row.updated_at for row in rows), default=None),
    }

@app.get("/api/bills")
def bills(
    billing_cycle: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    authorization: str = Header(default=""),
):
    require_config_token(authorization)
    return bill_snapshot(validate_cycle(billing_cycle), limit)

@app.post("/api/poll")
@app.post("/poll", include_in_schema=False)
def poll(billing_cycle: str | None = None, authorization: str = Header(default="")):
    require_config_token(authorization)
    cycle = validate_cycle(billing_cycle)
    try:
        states = run_poll(cycle)
        return {"states": states, "bills": bill_snapshot(cycle)}
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
