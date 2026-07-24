import json
from abc import ABC, abstractmethod
import httpx
from sqlalchemy import select
from budget_guard.adapters.volc import VolcOpenAPI
from budget_guard.db import ControlledResource, SessionLocal
from budget_guard.domain.models import SubsidiaryBudget


class Limiter(ABC):
    @abstractmethod
    def set_normal(self, budget: SubsidiaryBudget) -> None: ...

    @abstractmethod
    def throttle(self, budget: SubsidiaryBudget) -> None: ...

    @abstractmethod
    def block(self, budget: SubsidiaryBudget) -> None: ...


class LogLimiter(Limiter):
    def set_normal(self, budget: SubsidiaryBudget) -> None:
        print(f"NORMAL {budget.volc_project}")

    def throttle(self, budget: SubsidiaryBudget) -> None:
        print(f"THROTTLE {budget.volc_project} rps={budget.throttle_rps}")

    def block(self, budget: SubsidiaryBudget) -> None:
        print(f"BLOCK {budget.volc_project}")


class WebhookLimiter(Limiter):
    def __init__(self, url_template: str, token: str, dry_run: bool = True):
        self.url_template, self.token, self.dry_run = url_template, token, dry_run

    def _apply(self, budget: SubsidiaryBudget, state: str, rps: int | None = None) -> None:
        if self.dry_run:
            print(f"DRY_RUN limiter project={budget.volc_project} state={state} rps={rps}")
            return
        url = self.url_template.format(project_id=budget.volc_project)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = httpx.put(url, json={"state": state, "rps": rps}, headers=headers, timeout=10)
        response.raise_for_status()

    def set_normal(self, budget: SubsidiaryBudget) -> None:
        self._apply(budget, "normal")

    def throttle(self, budget: SubsidiaryBudget) -> None:
        self._apply(budget, "throttled", budget.throttle_rps)

    def block(self, budget: SubsidiaryBudget) -> None:
        self._apply(budget, "blocked", 0)


class VolcLimiter(Limiter):
    def __init__(
        self, access_key: str, secret_key: str, region: str,
        ark_endpoint: str, iam_endpoint: str, dry_run: bool = True,
    ):
        self.api = VolcOpenAPI(access_key, secret_key, region)
        self.ark_endpoint, self.iam_endpoint, self.dry_run = ark_endpoint, iam_endpoint, dry_run

    def _ark(self, action: str, body: dict) -> dict:
        return self.api.request(
            self.ark_endpoint, "ark", action, "2024-01-01", body=body
        ).get("Result", {})

    def _iam(self, action: str, query: dict) -> dict:
        return self.api.request(
            self.iam_endpoint, "iam", action, "2018-01-01", query=query
        ).get("Result", {})

    def _list_endpoints(self, project: str) -> list[dict]:
        page, items = 1, []
        while True:
            result = self._ark("ListEndpoints", {
                "PageNumber": page, "PageSize": 100, "ProjectName": project
            })
            items.extend(result.get("Items", []))
            if len(items) >= int(result.get("TotalCount", len(items))):
                return items
            page += 1

    def _record(
        self, project: str, resource_type: str, resource_id: str, detail: dict | None = None
    ) -> None:
        with SessionLocal.begin() as db:
            exists = db.scalar(select(ControlledResource).where(
                ControlledResource.project_id == project,
                ControlledResource.resource_type == resource_type,
                ControlledResource.resource_id == resource_id,
            ))
            if not exists:
                db.add(ControlledResource(
                    project_id=project, resource_type=resource_type, resource_id=resource_id,
                    detail=json.dumps(detail or {}, ensure_ascii=False),
                ))

    def block(self, budget: SubsidiaryBudget) -> None:
        project, control = budget.volc_project, budget.control
        if self.dry_run:
            print(
                f"DRY_RUN volc block project={project} "
                f"stop_endpoints={control.stop_endpoints_on_block} "
                f"disable_iam_keys={control.disable_iam_access_keys_on_block}"
            )
        if control.stop_endpoints_on_block:
            for endpoint in self._list_endpoints(project):
                if str(endpoint.get("Status", "")).lower() not in ("running", "available"):
                    continue
                endpoint_id = str(endpoint["Id"])
                if self.dry_run:
                    print(f"DRY_RUN would stop endpoint={endpoint_id} project={project}")
                    continue
                self._ark("StopEndpoint", {"Id": endpoint_id})
                self._record(project, "ark_endpoint", endpoint_id)
        if control.disable_iam_access_keys_on_block:
            metadata = self._iam(
                "ListAccessKeys", {"UserName": control.iam_user_name}
            ).get("AccessKeyMetadata", [])
            statuses = {item["AccessKeyId"]: item.get("Status") for item in metadata}
            for access_key_id in control.iam_access_key_ids:
                if access_key_id not in statuses:
                    raise RuntimeError(f"IAM access key not found: {access_key_id}")
                if statuses[access_key_id] != "active":
                    continue
                if self.dry_run:
                    print(
                        f"DRY_RUN would disable iam_access_key={access_key_id} "
                        f"user={control.iam_user_name}"
                    )
                    continue
                self._iam("UpdateAccessKey", {
                    "UserName": control.iam_user_name,
                    "AccessKeyId": access_key_id,
                    "Status": "inactive",
                })
                self._record(
                    project, "iam_access_key", access_key_id,
                    {"user_name": control.iam_user_name},
                )

    def set_normal(self, budget: SubsidiaryBudget) -> None:
        project = budget.volc_project
        with SessionLocal() as db:
            resources = list(db.scalars(select(ControlledResource).where(
                ControlledResource.project_id == project
            )))
        if self.dry_run:
            print(f"DRY_RUN volc restore project={project} resources={len(resources)}")
            return
        for resource in resources:
            if resource.resource_type == "ark_endpoint":
                self._ark("StartEndpoint", {"Id": resource.resource_id})
            elif resource.resource_type == "iam_access_key":
                detail = json.loads(resource.detail or "{}")
                self._iam("UpdateAccessKey", {
                    "UserName": detail["user_name"],
                    "AccessKeyId": resource.resource_id,
                    "Status": "active",
                })
            with SessionLocal.begin() as db:
                row = db.get(ControlledResource, resource.id)
                if row:
                    db.delete(row)

    def throttle(self, budget: SubsidiaryBudget) -> None:
        print(f"THROTTLE {budget.volc_project} requires gateway control")
