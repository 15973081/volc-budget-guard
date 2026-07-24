from decimal import Decimal
from datetime import date
import json
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from budget_guard.adapters.billing import VolcBillingProvider, currency, project_id, unique_key
from budget_guard.adapters.limiter import VolcLimiter
from budget_guard.api import main as api_main
from budget_guard.api.main import app
from budget_guard.config import settings
from budget_guard.db import Base, BillDetail
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
    assert unique_key({**row, "SplitItemID": "item-1", "ChargeItemCode": "gpu"}) != unique_key(
        {**row, "SplitItemID": "item-1", "ChargeItemCode": "storage"}
    )

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
    limiter = VolcLimiter("ak", "sk", "cn-beijing", "https://ark", "https://iam", True)
    monkeypatch.setattr(limiter, "project_status", lambda budget: {
        "dry_run": True, "endpoints": [{"id": "ep-1", "status": "Running"}],
        "access_keys": [],
    })
    monkeypatch.setattr(limiter, "set_endpoints", lambda project, enabled: ["ep-1"])
    monkeypatch.setattr(api_main, "limiter_provider", lambda: limiter)
    client = TestClient(app)

    admin = client.get("/admin")
    assert admin.status_code == 200
    assert "内部子公司 ID" not in admin.text
    assert "项目消费金额" in admin.text
    assert client.get("/api/config").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/api/config", headers=headers).json() == payload
    assert client.put("/api/config", headers=headers, json=payload).json() == {"status": "saved"}
    assert client.get("/api/control/company-a", headers=headers).json()["dry_run"] is True
    result = client.post(
        "/api/control/company-a/endpoints?enabled=false", headers=headers
    ).json()
    assert result["changed"] == ["ep-1"]
    assert client.post(
        "/api/control/company-a/all?enabled=false", headers=headers
    ).json()["changed"] == ["ep-1"]


def test_bill_query_and_manual_poll(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(engine, expire_on_commit=False)
    config_path = tmp_path / "budgets.yaml"
    save_budget_config(config_path, {"subsidiaries": {"company-a": {
        "volc_project": "project-a", "currency": "CNY",
        "warning_ratio": "0.8", "budgets": {"monthly": {"amount": "100"}},
    }}})
    with test_session.begin() as db:
        db.add(BillDetail(
            unique_key="test-bill", billing_cycle="2099-01", project_id="project-a",
            amount=Decimal("12.50"), resource_id="i-1", product="云服务器",
            raw_json=json.dumps({"Currency": "CNY"}),
        ))
    monkeypatch.setattr(api_main, "SessionLocal", test_session)
    monkeypatch.setattr(api_main, "run_poll", lambda cycle: {"company-a": {"state": "normal"}})
    monkeypatch.setattr(settings, "budget_config_path", config_path)
    monkeypatch.setattr(settings, "config_api_token", "test-token")
    headers = {"Authorization": "Bearer test-token"}
    client = TestClient(app)

    bills = client.get("/api/bills?billing_cycle=2099-01", headers=headers).json()
    assert bills["totals"] == {"CNY": "12.50000000"}
    assert Decimal(bills["budget_metrics"]["company-a"]["warning_left"]) == Decimal("67.5")
    result = client.post("/api/poll?billing_cycle=2099-01", headers=headers).json()
    assert result["states"]["company-a"]["state"] == "normal"
    assert result["bills"]["count"] == 1


def test_volc_billing_request_is_signed(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"Result": {"List": [], "Total": 0}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr("budget_guard.adapters.volc.httpx.request", fake_request)
    provider = VolcBillingProvider("ak", "sk", "https://billing.volcengineapi.com", "cn-north-1")
    provider._request_page("2026-07", 0, 300)

    assert captured["headers"]["Authorization"].startswith("HMAC-SHA256 Credential=ak/")
    assert json.loads(captured["content"])["BillPeriod"] == "2026-07"
