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
class ProjectControl:
    stop_endpoints_on_block: bool = False
    disable_iam_access_keys_on_block: bool = False
    iam_user_name: str = ""
    iam_access_key_ids: tuple[str, ...] = ()
    block_gateway_on_block: bool = False

@dataclass(frozen=True)
class SubsidiaryBudget:
    subsidiary_id: str
    company_name: str
    volc_project: str
    currency: str
    budgets: dict[str, BudgetLimit]
    throttle_rps: int
    throttle_concurrency: int
    enabled: bool = True
    project_start_date: date | None = None
    control: ProjectControl = ProjectControl()
