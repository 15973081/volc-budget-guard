from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from budget_guard.db import init_db, SessionLocal, EnforcementEvent
from budget_guard.factory import billing_provider, limiter_provider
from budget_guard.services.poller import BillingPoller

app = FastAPI(title="Budget Guard", version="0.1.0")

@app.on_event("startup")
def startup(): init_db()

@app.get("/health")
def health(): return {"status": "ok"}

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
            result.append({
                "project_id": row.project_id,
                "budget_type": budget_type if separator else "monthly",
                "window_key": window_key if separator else budget_type,
                "state": row.state,
                "amount": str(row.amount),
                "budget": str(row.budget),
                "created_at": row.created_at,
            })
        return result
