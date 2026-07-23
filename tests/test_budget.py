from decimal import Decimal
from budget_guard.domain.models import ProjectBudget, EnforcementState
from budget_guard.services.budgets import decide_state

B = ProjectBudget("p","P",Decimal("100"),Decimal("0.8"),Decimal("0.95"),Decimal("1"),1)

def test_states():
    assert decide_state(Decimal("79"), B) == EnforcementState.NORMAL
    assert decide_state(Decimal("80"), B) == EnforcementState.WARNING
    assert decide_state(Decimal("95"), B) == EnforcementState.THROTTLED
    assert decide_state(Decimal("100"), B) == EnforcementState.BLOCKED
