from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

class EnforcementState(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    THROTTLED = "throttled"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class BudgetLimit:
    amount: Decimal
    warning_ratio: Decimal
    throttle_ratio: Decimal
    block_ratio: Decimal

@dataclass(frozen=True)
class ProjectBudget:
    project_id: str
    name: str
    budgets: dict[str, BudgetLimit]
    throttle_rps: int
    enabled: bool = True
    project_start_date: date | None = None

@dataclass(frozen=True)
class ProjectSpend:
    project_id: str
    billing_cycle: str
    amount: Decimal
