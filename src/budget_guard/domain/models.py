from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

class EnforcementState(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    THROTTLED = "throttled"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class ProjectBudget:
    project_id: str
    name: str
    monthly_budget: Decimal
    warning_ratio: Decimal
    throttle_ratio: Decimal
    block_ratio: Decimal
    throttle_rps: int
    enabled: bool = True

@dataclass(frozen=True)
class ProjectSpend:
    project_id: str
    billing_cycle: str
    amount: Decimal
