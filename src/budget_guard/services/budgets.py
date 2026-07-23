from decimal import Decimal
from pathlib import Path
import yaml
from budget_guard.domain.models import ProjectBudget, EnforcementState

def load_budgets(path: Path) -> dict[str, ProjectBudget]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = {}
    for project_id, item in data.get("projects", {}).items():
        result[project_id] = ProjectBudget(
            project_id=project_id, name=item.get("name", project_id),
            monthly_budget=Decimal(str(item["monthly_budget"])),
            warning_ratio=Decimal(str(item.get("warning_ratio", "0.8"))),
            throttle_ratio=Decimal(str(item.get("throttle_ratio", "0.95"))),
            block_ratio=Decimal(str(item.get("block_ratio", "1.0"))),
            throttle_rps=int(item.get("throttle_rps", 1)), enabled=bool(item.get("enabled", True)),
        )
    return result

def decide_state(amount: Decimal, budget: ProjectBudget) -> EnforcementState:
    ratio = amount / budget.monthly_budget if budget.monthly_budget > 0 else Decimal("999")
    if ratio >= budget.block_ratio: return EnforcementState.BLOCKED
    if ratio >= budget.throttle_ratio: return EnforcementState.THROTTLED
    if ratio >= budget.warning_ratio: return EnforcementState.WARNING
    return EnforcementState.NORMAL
