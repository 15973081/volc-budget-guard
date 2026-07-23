from decimal import Decimal
from datetime import date
from pathlib import Path
import yaml
from budget_guard.domain.models import BudgetLimit, SubsidiaryBudget, EnforcementState

PERIODS = ("monthly", "quarterly", "yearly", "total")

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

def load_budgets(path: Path) -> dict[str, SubsidiaryBudget]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = {}
    used_projects = set()
    subsidiaries = data.get("subsidiaries")
    if subsidiaries is None:  # Backward-compatible project-only format.
        subsidiaries = data.get("projects", {})
    for subsidiary_id, item in subsidiaries.items():
        volc_project = str(item.get("volc_project") or subsidiary_id)
        if volc_project in used_projects:
            raise ValueError(f"volc project {volc_project} is assigned to multiple subsidiaries")
        used_projects.add(volc_project)
        configured = item.get("budgets")
        if configured is None:  # Backward-compatible monthly-only format.
            configured = {"monthly": {
                "amount": item["monthly_budget"],
                "warning_ratio": item.get("warning_ratio", "0.8"),
                "throttle_ratio": item.get("throttle_ratio", "0.95"),
                "block_ratio": item.get("block_ratio", "1.0"),
            }}
        if not configured:
            raise ValueError(f"subsidiary {subsidiary_id} must configure at least one budget")
        normalized = {}
        for period, value in configured.items():
            period = "total" if period == "lifetime" else period
            if period in normalized:
                raise ValueError(f"duplicate total budget for subsidiary {subsidiary_id}")
            normalized[period] = {
                "amount": value["amount"],
                "warning_ratio": value.get("warning_ratio", item.get("warning_ratio", "0.8")),
                "throttle_ratio": value.get("throttle_ratio", item.get("throttle_ratio", "0.95")),
                "block_ratio": value.get("block_ratio", item.get("block_ratio", "1.0")),
            }
        unknown = set(normalized) - set(PERIODS)
        if unknown:
            raise ValueError(f"unsupported budget periods: {', '.join(sorted(unknown))}")
        start = item.get("project_start_date")
        currency = str(item.get("currency", "CNY")).upper()
        if not currency:
            raise ValueError(f"subsidiary {subsidiary_id} must configure a currency")
        result[subsidiary_id] = SubsidiaryBudget(
            subsidiary_id=subsidiary_id,
            company_name=item.get("company_name") or item.get("name", subsidiary_id),
            volc_project=volc_project,
            currency=currency,
            budgets={period: _load_limit(value) for period, value in normalized.items()},
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
    if period in ("total", "lifetime"):
        return "total", project_start_date.strftime("%Y-%m") if project_start_date else "0000-01"
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
