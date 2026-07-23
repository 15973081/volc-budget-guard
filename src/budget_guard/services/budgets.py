from decimal import Decimal
from datetime import date
from pathlib import Path
import yaml
from budget_guard.domain.models import BudgetLimit, ProjectBudget, EnforcementState

PERIODS = ("monthly", "quarterly", "yearly", "lifetime")

def _load_limit(item: dict) -> BudgetLimit:
    limit = BudgetLimit(
        amount=Decimal(str(item["amount"])),
        warning_ratio=Decimal(str(item.get("warning_ratio", "0.8"))),
        throttle_ratio=Decimal(str(item.get("throttle_ratio", "0.95"))),
        block_ratio=Decimal(str(item.get("block_ratio", "1.0"))),
    )
    if limit.amount <= 0:
        raise ValueError("budget amount must be greater than zero")
    if not Decimal("0") <= limit.warning_ratio <= limit.throttle_ratio <= limit.block_ratio:
        raise ValueError("budget ratios must satisfy 0 <= warning <= throttle <= block")
    return limit

def load_budgets(path: Path) -> dict[str, ProjectBudget]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = {}
    for project_id, item in data.get("projects", {}).items():
        configured = item.get("budgets")
        if configured is None:  # Backward-compatible monthly-only format.
            configured = {"monthly": {
                "amount": item["monthly_budget"],
                "warning_ratio": item.get("warning_ratio", "0.8"),
                "throttle_ratio": item.get("throttle_ratio", "0.95"),
                "block_ratio": item.get("block_ratio", "1.0"),
            }}
        if not configured:
            raise ValueError(f"project {project_id} must configure at least one budget")
        unknown = set(configured) - set(PERIODS)
        if unknown:
            raise ValueError(f"unsupported budget periods: {', '.join(sorted(unknown))}")
        start = item.get("project_start_date")
        result[project_id] = ProjectBudget(
            project_id=project_id, name=item.get("name", project_id),
            budgets={period: _load_limit(value) for period, value in configured.items()},
            throttle_rps=int(item.get("throttle_rps", 1)), enabled=bool(item.get("enabled", True)),
            project_start_date=date.fromisoformat(str(start)) if start else None,
        )
    return result

def budget_window(period: str, cycle: str, project_start_date: date | None = None) -> tuple[str, str]:
    year, month = map(int, cycle.split("-"))
    if not 1 <= month <= 12:
        raise ValueError(f"invalid billing cycle: {cycle}")
    if period == "monthly":
        return cycle, cycle
    if period == "quarterly":
        quarter = (month - 1) // 3 + 1
        return f"{year}-Q{quarter}", f"{year}-{(quarter - 1) * 3 + 1:02d}"
    if period == "yearly":
        return str(year), f"{year}-01"
    if period == "lifetime":
        return "lifetime", project_start_date.strftime("%Y-%m") if project_start_date else "0000-01"
    raise ValueError(f"unsupported budget period: {period}")

def decide_state(amount: Decimal, budget: BudgetLimit) -> EnforcementState:
    ratio = amount / budget.amount
    if ratio >= budget.block_ratio:
        return EnforcementState.BLOCKED
    if ratio >= budget.throttle_ratio:
        return EnforcementState.THROTTLED
    if ratio >= budget.warning_ratio:
        return EnforcementState.WARNING
    return EnforcementState.NORMAL
