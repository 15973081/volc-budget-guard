import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from budget_guard.db import SessionLocal, BillDetail, EnforcementEvent
from budget_guard.adapters.billing import BillingProvider, payable_amount, project_id, unique_key
from budget_guard.adapters.limiter import Limiter
from budget_guard.services.budgets import load_budgets, decide_state
from budget_guard.config import settings
from budget_guard.domain.models import EnforcementState

class BillingPoller:
    def __init__(self, billing: BillingProvider, limiter: Limiter):
        self.billing, self.limiter = billing, limiter

    def run_once(self, billing_cycle: str | None = None) -> dict[str, str]:
        cycle = billing_cycle or datetime.now().strftime("%Y-%m")
        rows = self.billing.list_split_bill_details(cycle)
        with SessionLocal.begin() as db:
            for row in rows:
                key = unique_key(row)
                existing = db.scalar(select(BillDetail).where(BillDetail.unique_key == key))
                values = dict(billing_cycle=cycle, project_id=project_id(row), amount=payable_amount(row),
                              resource_id=str(row.get("InstanceNo") or ""), product=str(row.get("ProductZh") or row.get("Product") or ""),
                              raw_json=json.dumps(row, ensure_ascii=False))
                if existing:
                    for k, v in values.items(): setattr(existing, k, v)
                else:
                    db.add(BillDetail(unique_key=key, **values))

        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        with SessionLocal() as db:
            for pid, amount in db.execute(select(BillDetail.project_id, BillDetail.amount).where(BillDetail.billing_cycle == cycle)):
                totals[pid] += Decimal(str(amount))

        budgets = load_budgets(settings.budget_config_path)
        states: dict[str, str] = {}
        with SessionLocal.begin() as db:
            for pid, budget in budgets.items():
                if not budget.enabled: continue
                amount = totals.get(pid, Decimal("0"))
                state = decide_state(amount, budget)
                states[pid] = state.value
                exists = db.scalar(select(EnforcementEvent).where(
                    EnforcementEvent.project_id == pid,
                    EnforcementEvent.billing_cycle == cycle,
                    EnforcementEvent.state == state.value,
                ))
                if not exists:
                    if state == EnforcementState.BLOCKED: self.limiter.block(pid)
                    elif state == EnforcementState.THROTTLED: self.limiter.throttle(pid, budget.throttle_rps)
                    elif state == EnforcementState.NORMAL: self.limiter.set_normal(pid)
                    db.add(EnforcementEvent(project_id=pid, billing_cycle=cycle, state=state.value,
                        amount=amount, budget=budget.monthly_budget,
                        detail=f"ratio={(amount / budget.monthly_budget if budget.monthly_budget else 0):.4f}"))
        return states
