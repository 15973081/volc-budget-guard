from decimal import Decimal
from datetime import date
from budget_guard.domain.models import BudgetLimit, EnforcementState
from budget_guard.services.budgets import budget_window, decide_state

B = BudgetLimit(Decimal("100"), Decimal("0.8"), Decimal("0.95"), Decimal("1"))

def test_states():
    assert decide_state(Decimal("79"), B) == EnforcementState.NORMAL
    assert decide_state(Decimal("80"), B) == EnforcementState.WARNING
    assert decide_state(Decimal("95"), B) == EnforcementState.THROTTLED
    assert decide_state(Decimal("100"), B) == EnforcementState.BLOCKED

def test_budget_windows():
    assert budget_window("monthly", "2026-07") == ("2026-07", "2026-07")
    assert budget_window("quarterly", "2026-07") == ("2026-Q3", "2026-07")
    assert budget_window("yearly", "2026-07") == ("2026", "2026-01")
    assert budget_window("lifetime", "2026-07", date(2025, 11, 1)) == ("lifetime", "2025-11")
