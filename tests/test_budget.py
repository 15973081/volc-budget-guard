from decimal import Decimal
from datetime import date
from budget_guard.adapters.billing import currency, project_id, unique_key
from budget_guard.domain.models import BudgetLimit, EnforcementState
from budget_guard.services.budgets import budget_window, decide_state, load_budgets

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
    assert budget_window("total", "2026-07", date(2025, 11, 1)) == ("total", "2025-11")

def test_subsidiary_project_mapping(tmp_path):
    path = tmp_path / "budgets.yaml"
    path.write_text("""
subsidiaries:
  company-a:
    company_name: 子公司A
    volc_project: project-a
    currency: cny
    warning_ratio: "0.8"
    budgets:
      monthly: {amount: "100"}
      total: {amount: "500"}
""", encoding="utf-8")
    budget = load_budgets(path)["company-a"]
    assert budget.volc_project == "project-a"
    assert budget.currency == "CNY"
    assert budget.budgets["monthly"].warning_ratio == Decimal("0.8")

def test_official_bill_fields():
    row = {
        "BillPeriod": "2026-07",
        "SplitBillDetailId": "detail-1",
        "Project": "project-a",
        "Currency": "cny",
    }
    assert project_id(row) == "project-a"
    assert currency(row) == "CNY"
    assert "detail-1" in unique_key(row)
