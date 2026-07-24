from decimal import Decimal
from datetime import date
from fastapi.testclient import TestClient
from budget_guard.adapters.billing import currency, project_id, unique_key
from budget_guard.api.main import app
from budget_guard.config import settings
from budget_guard.domain.models import BudgetLimit, EnforcementState
from budget_guard.services.budgets import (
    budget_window,
    decide_state,
    load_budgets,
    read_budget_config,
    save_budget_config,
)

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

def test_config_save_is_validated_and_atomic(tmp_path):
    path = tmp_path / "budgets.yaml"
    valid = {
        "subsidiaries": {
            "company-a": {
                "company_name": "子公司A",
                "volc_project": "project-a",
                "currency": "CNY",
                "budgets": {"monthly": {"amount": "100"}},
            }
        }
    }
    save_budget_config(path, valid)
    assert read_budget_config(path) == valid

    invalid = {"subsidiaries": {"company-a": {"budgets": {}}}}
    try:
        save_budget_config(path, invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid configuration was accepted")
    assert read_budget_config(path) == valid

def test_admin_config_api(tmp_path, monkeypatch):
    path = tmp_path / "budgets.yaml"
    payload = {
        "subsidiaries": {
            "company-a": {
                "volc_project": "project-a",
                "budgets": {"monthly": {"amount": "100"}},
            }
        }
    }
    save_budget_config(path, payload)
    monkeypatch.setattr(settings, "budget_config_path", path)
    monkeypatch.setattr(settings, "config_api_token", "test-token")
    client = TestClient(app)

    assert client.get("/admin").status_code == 200
    assert client.get("/api/config").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/api/config", headers=headers).json() == payload
    assert client.put("/api/config", headers=headers, json=payload).json() == {"status": "saved"}
