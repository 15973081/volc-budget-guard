from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any
from budget_guard.adapters.volc import VolcOpenAPI

class BillingProvider(ABC):
    @abstractmethod
    def list_split_bill_details(self, billing_cycle: str) -> list[dict[str, Any]]: ...

class VolcBillingProvider(BillingProvider):
    def __init__(self, access_key: str, secret_key: str, endpoint: str, region: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint
        self.region = region

    def _request_page(self, billing_cycle: str, offset: int, limit: int) -> dict[str, Any]:
        return VolcOpenAPI(self.access_key, self.secret_key, self.region).request(
            self.endpoint, "billing", "ListSplitBillDetail", "2022-01-01",
            body={
                "BillPeriod": billing_cycle,
                "Offset": offset,
                "Limit": limit,
                "NeedRecordNum": 1,
            },
        )

    def list_split_bill_details(self, billing_cycle: str) -> list[dict[str, Any]]:
        offset, limit, rows = 0, 300, []
        while True:
            response = self._request_page(billing_cycle, offset, limit)
            result = response.get("Result", {})
            page = result.get("List", [])
            rows.extend(page)
            total = int(result.get("Total", len(rows)))
            if not page or len(rows) >= total:
                return rows
            offset += limit

def payable_amount(row: dict[str, Any]) -> Decimal:
    return Decimal(str(row.get("PayableAmount") or row.get("PaidAmount") or "0"))

def project_id(row: dict[str, Any]) -> str:
    return str(row.get("Project") or row.get("ProjectID") or row.get("ProjectId") or "UNASSIGNED")

def currency(row: dict[str, Any]) -> str:
    return str(row.get("Currency") or "").upper()

def unique_key(row: dict[str, Any], billing_cycle: str = "") -> str:
    fields = [
        row.get("BillPeriod") or billing_cycle, row.get("SplitBillDetailId"),
        row.get("BillDetailId"), row.get("CostID"), row.get("BillID"),
        row.get("SplitItemID"), row.get("ChargeItemCode"), row.get("InstanceNo"),
        project_id(row), row.get("AmortizedDay"),
    ]
    return "|".join(str(v or "") for v in fields)

def legacy_unique_key(row: dict[str, Any]) -> str:
    fields = [row.get("CostID"), row.get("BillDetailId"), row.get("BillID"), row.get("InstanceNo"), row.get("ProjectID"), row.get("AmortizedDay")]
    return "|".join(str(v or "") for v in fields)
