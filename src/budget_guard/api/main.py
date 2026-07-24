import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from secrets import compare_digest
from typing import Literal
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
import yaml
from budget_guard.adapters.limiter import VolcLimiter
from budget_guard.config import settings
from budget_guard.db import init_db, SessionLocal, BillDetail, EnforcementEvent
from budget_guard.factory import billing_provider, limiter_provider
from budget_guard.services.budgets import (
    budget_window, load_budgets, read_budget_config, save_budget_config,
)
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

def _control_target(subsidiary_id: str):
    budget = load_budgets(settings.budget_config_path).get(subsidiary_id)
    if not budget:
        raise HTTPException(404, "subsidiary not found")
    limiter = limiter_provider()
    if not isinstance(limiter, VolcLimiter):
        raise HTTPException(503, "LIMITER_PROVIDER must be volc")
    return budget, limiter

@app.get("/api/control/{subsidiary_id}")
def control_status(subsidiary_id: str, authorization: str = Header(default="")):
    require_config_token(authorization)
    budget, limiter = _control_target(subsidiary_id)
    return limiter.project_status(budget)

@app.post("/api/control/{subsidiary_id}/{resource}")
def update_control(
    subsidiary_id: str,
    resource: Literal["endpoints", "iam", "gateway", "all"],
    enabled: bool,
    authorization: str = Header(default=""),
):
    require_config_token(authorization)
    budget, limiter = _control_target(subsidiary_id)
    try:
        changed = []
        if resource in ("iam", "all"):
            control = budget.control
            if control.iam_user_name and control.iam_access_key_ids:
                changed.extend(limiter.set_access_keys(
                    control.iam_user_name, control.iam_access_key_ids, enabled
                ))
            elif resource == "iam":
                raise ValueError("IAM user name and access key IDs are required")
        if resource in ("endpoints", "all"):
            changed.extend(limiter.set_endpoints(budget.volc_project, enabled))
        if resource in ("gateway", "all"):
            if limiter.gateway.url_template:
                changed.extend(limiter.set_gateway(budget, enabled))
            elif resource == "gateway":
                raise ValueError("LIMITER_WEBHOOK_URL is not configured")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "dry_run": limiter.dry_run,
        "changed": changed,
        "status": limiter.project_status(budget),
    }

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
    project_cycles = defaultdict(lambda: Decimal("0"))
    items = []
    with SessionLocal() as db:
        rows = db.scalars(
            select(BillDetail)
            .where(BillDetail.billing_cycle == billing_cycle)
            .order_by(BillDetail.updated_at.desc(), BillDetail.id.desc())
        ).all()
        history = db.execute(
            select(BillDetail.project_id, BillDetail.billing_cycle, BillDetail.amount)
        ).all()
    for project, cycle, amount in history:
        project_cycles[project, cycle] += Decimal(str(amount))
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
    budget_metrics = {}
    for subsidiary_id, budget in load_budgets(settings.budget_config_path).items():
        candidates = []
        for period, budget_limit in budget.budgets.items():
            window_key, start_cycle = budget_window(
                period, billing_cycle, budget.project_start_date
            )
            spent = sum(
                (
                    value for (project, cycle), value in project_cycles.items()
                    if project == budget.volc_project
                    and start_cycle <= cycle <= billing_cycle
                ),
                Decimal("0"),
            )
            warning = budget_limit.amount * budget_limit.warning_ratio
            candidates.append({
                "period": period,
                "window_key": window_key,
                "currency": budget.currency,
                "budget": str(budget_limit.amount),
                "spent": str(spent),
                "warning_left": str(max(warning - spent, Decimal("0"))),
                "ratio": str(spent / budget_limit.amount),
            })
        if candidates:
            budget_metrics[subsidiary_id] = max(
                candidates, key=lambda item: Decimal(item["ratio"])
            )
    return {
        "billing_cycle": billing_cycle,
        "count": len(rows),
        "totals": {currency: str(amount) for currency, amount in totals.items()},
        "projects": [
            {"project": project, "currency": currency, "amount": str(amount)}
            for (project, currency), amount in sorted(projects.items())
        ],
        "budget_metrics": budget_metrics,
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
