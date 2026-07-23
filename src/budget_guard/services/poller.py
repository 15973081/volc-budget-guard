import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from budget_guard.db import SessionLocal, BillDetail, EnforcementEvent, AppliedState
from budget_guard.adapters.billing import BillingProvider, payable_amount, project_id, unique_key
from budget_guard.adapters.limiter import Limiter
from budget_guard.services.budgets import load_budgets, budget_window, decide_state
from budget_guard.config import settings
from budget_guard.domain.models import EnforcementState

STATE_PRIORITY = {
    EnforcementState.NORMAL: 0,
    EnforcementState.WARNING: 1,
    EnforcementState.THROTTLED: 2,
    EnforcementState.BLOCKED: 3,
}
PERIOD_PRIORITY = {"monthly": 0, "quarterly": 1, "yearly": 2, "lifetime": 3}

class BillingPoller:
    def __init__(self, billing: BillingProvider, limiter: Limiter):
        self.billing, self.limiter = billing, limiter

    def run_once(self, billing_cycle: str | None = None) -> dict[str, str]:
        current_cycle = datetime.now().strftime("%Y-%m")
        cycle = billing_cycle or current_cycle
        rows = self.billing.list_split_bill_details(cycle)
        with SessionLocal.begin() as db:
            for row in rows:
                key = unique_key(row, cycle)
                existing = db.scalar(select(BillDetail).where(BillDetail.unique_key == key))
                if not existing:
                    legacy_key = key.partition("|")[2]
                    existing = db.scalar(select(BillDetail).where(BillDetail.unique_key == legacy_key))
                    if existing:
                        existing.unique_key = key
                values = dict(billing_cycle=cycle, project_id=project_id(row), amount=payable_amount(row),
                              resource_id=str(row.get("InstanceNo") or ""), product=str(row.get("ProductZh") or row.get("Product") or ""),
                              raw_json=json.dumps(row, ensure_ascii=False))
                if existing:
                    for k, v in values.items():
                        setattr(existing, k, v)
                else:
                    db.add(BillDetail(unique_key=key, **values))

        totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        with SessionLocal() as db:
            for pid, bill_cycle, amount in db.execute(
                select(BillDetail.project_id, BillDetail.billing_cycle, BillDetail.amount)
            ):
                totals[pid, bill_cycle] += Decimal(str(amount))

        budgets = load_budgets(settings.budget_config_path)
        states: dict[str, str] = {}
        with SessionLocal.begin() as db:
            for pid, budget in budgets.items():
                if not budget.enabled:
                    continue
                evaluations = []
                for period, limit in budget.budgets.items():
                    window_key, start_cycle = budget_window(period, cycle, budget.project_start_date)
                    amount = sum(
                        (value for (row_pid, row_cycle), value in totals.items()
                         if row_pid == pid and start_cycle <= row_cycle <= cycle),
                        Decimal("0"),
                    )
                    state = decide_state(amount, limit)
                    evaluations.append((state, PERIOD_PRIORITY[period], period, window_key, amount, limit))
                if not evaluations:
                    continue
                state, _, period, window_key, amount, limit = max(
                    evaluations, key=lambda item: (STATE_PRIORITY[item[0]], item[1])
                )
                states[pid] = state.value
                if cycle != current_cycle:
                    continue
                applied = db.get(AppliedState, pid)
                changed = applied is None or applied.state != state.value
                if changed:
                    if state == EnforcementState.BLOCKED:
                        self.limiter.block(pid)
                    elif state == EnforcementState.THROTTLED:
                        self.limiter.throttle(pid, budget.throttle_rps)
                    elif state == EnforcementState.NORMAL or (
                        applied and applied.state in (EnforcementState.THROTTLED, EnforcementState.BLOCKED)
                    ):
                        self.limiter.set_normal(pid)
                    event_cycle = f"{period}:{window_key}"
                    exists = db.scalar(select(EnforcementEvent).where(
                        EnforcementEvent.project_id == pid,
                        EnforcementEvent.billing_cycle == event_cycle,
                        EnforcementEvent.state == state.value,
                    ))
                    if not exists:
                        db.add(EnforcementEvent(
                            project_id=pid, billing_cycle=event_cycle, state=state.value,
                            amount=amount, budget=limit.amount,
                            detail=json.dumps({
                                "budget_type": period,
                                "window_key": window_key,
                                "ratio": str(amount / limit.amount),
                            }),
                        ))
                if applied:
                    applied.state, applied.budget_type, applied.window_key = state.value, period, window_key
                else:
                    db.add(AppliedState(
                        project_id=pid, state=state.value, budget_type=period, window_key=window_key
                    ))
        return states
