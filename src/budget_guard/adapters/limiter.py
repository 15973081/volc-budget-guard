import json
from abc import ABC, abstractmethod
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


class VolcLimiter(Limiter):
    def __init__(
        self, access_key: str, secret_key: str, region: str,
        ark_endpoint: str, dry_run: bool = True,
    ):
        self.api = VolcOpenAPI(access_key, secret_key, region)
        self.ark_endpoint, self.dry_run = ark_endpoint, dry_run

    def _ark(self, action: str, body: dict) -> dict:
        return self.api.request(
            self.ark_endpoint, "ark", action, "2024-01-01", body=body
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

    def _list_preset_endpoints(self, project: str) -> list[dict]:
        page, items = 1, []
        while True:
            result = self._ark("InnerDescribeModelEndpoints", {
                "PageNumber": page, "PageSize": 100, "ProjectName": project
            })
            items.extend(result.get("Items", []))
            if len(items) >= int(result.get("TotalCount", len(items))):
                return items
            page += 1

    def project_status(self, budget: SubsidiaryBudget) -> dict:
        endpoints = self._list_endpoints(budget.volc_project)
        preset_endpoints = self._list_preset_endpoints(budget.volc_project)
        return {
            "dry_run": self.dry_run,
            "endpoints": [
                {"id": item["Id"], "status": item.get("Status", "unknown"), "managed": True}
                for item in endpoints
            ],
            "preset_endpoints": [
                {
                    "id": item["Id"],
                    "model_id": item.get("ModelId", item.get("Name", "")),
                    "status": item.get("Status", "unknown"),
                    "managed": True,
                }
                for item in preset_endpoints
            ],
        }

    def set_endpoints(
        self, project: str, enabled: bool, track: bool = False
    ) -> list[str]:
        target_statuses = {"stopped"} if enabled else {"running", "available"}
        action = "StartEndpoint" if enabled else "StopEndpoint"
        verb = "start" if enabled else "stop"
        changed = []
        for endpoint in self._list_endpoints(project):
            if str(endpoint.get("Status", "")).lower() not in target_statuses:
                continue
            endpoint_id = str(endpoint["Id"])
            changed.append(endpoint_id)
            if self.dry_run:
                print(f"DRY_RUN would {verb} endpoint={endpoint_id} project={project}")
                continue
            self._ark(action, {"Id": endpoint_id})
            if track and not enabled:
                self._record(project, "ark_endpoint", endpoint_id)
        return changed

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

    def set_endpoint_limits(
        self, project: str, concurrency: int, rpm: int, target: str = "all"
    ) -> list[str]:
        if target not in {"endpoints", "preset_endpoints", "all"}:
            raise ValueError(f"unsupported endpoint limit target: {target}")
        changed = []
        content_generation = {
            "ConcurrentRequests": concurrency,
            "CreateTaskRpm": rpm,
        }
        endpoints = self._list_endpoints(project) if target != "preset_endpoints" else []
        preset_endpoints = (
            self._list_preset_endpoints(project) if target != "endpoints" else []
        )
        for endpoint in endpoints:
            endpoint_id = str(endpoint["Id"])
            changed.append(endpoint_id)
            if self.dry_run:
                print(
                    f"DRY_RUN would limit endpoint={endpoint_id} project={project} "
                    f"concurrency={concurrency} rpm={rpm}"
                )
                continue
            current = self._ark("GetEndpoint", {"Id": endpoint_id})
            self._record(
                project, "ark_endpoint_content_generation", endpoint_id,
                {"content_generation": current.get("ContentGeneration")},
            )
            self._ark("UpdateEndpoint", {
                "Id": endpoint_id,
                "ContentGeneration": content_generation,
            })
        for endpoint in preset_endpoints:
            endpoint_id = str(endpoint["Id"])
            changed.append(endpoint_id)
            if self.dry_run:
                print(
                    f"DRY_RUN would limit preset_endpoint={endpoint_id} project={project} "
                    f"concurrency={concurrency} rpm={rpm}"
                )
                continue
            current = self._ark(
                "InnerDescribeModelEndpointDetail", {"Id": endpoint_id}
            )
            current = current.get("Endpoint", current)
            self._record(
                project, "ark_preset_endpoint_content_generation", endpoint_id,
                {"content_generation": current.get("ContentGeneration")},
            )
            self._ark("InnerUpdateModelEndpoint", {
                "Id": endpoint_id,
                "ContentGeneration": content_generation,
            })
        return changed

    def restore_endpoint_limits(self, project: str, target: str = "all") -> list[str]:
        types = {
            "endpoints": {"ark_endpoint_content_generation"},
            "preset_endpoints": {"ark_preset_endpoint_content_generation"},
            "all": {
                "ark_endpoint_content_generation",
                "ark_preset_endpoint_content_generation",
            },
        }
        if target not in types:
            raise ValueError(f"unsupported endpoint limit target: {target}")
        with SessionLocal() as db:
            resources = [resource for resource in db.scalars(select(ControlledResource).where(
                ControlledResource.project_id == project
            )) if resource.resource_type in types[target]]
        if self.dry_run:
            print(f"DRY_RUN would restore endpoint limits project={project} target={target}")
            return [resource.resource_id for resource in resources]
        for resource in resources:
            detail = json.loads(resource.detail or "{}")
            if resource.resource_type == "ark_endpoint_content_generation":
                content_generation = detail.get("content_generation")
                self._ark("UpdateEndpoint", {
                    "Id": resource.resource_id,
                    "ContentGeneration": content_generation
                    if content_generation is not None
                    else {"CreateTaskRpm": -1},
                })
            else:
                self._ark("InnerUpdateModelEndpoint", {
                    "Id": resource.resource_id,
                    "ContentGeneration": detail.get("content_generation"),
                })
            with SessionLocal.begin() as db:
                row = db.get(ControlledResource, resource.id)
                if row:
                    db.delete(row)
        return [resource.resource_id for resource in resources]

    def block(self, budget: SubsidiaryBudget) -> None:
        project, control = budget.volc_project, budget.control
        if self.dry_run:
            print(
                f"DRY_RUN volc block project={project} "
                f"limit_endpoints={control.stop_endpoints_on_block}"
            )
        if control.stop_endpoints_on_block:
            self.set_endpoint_limits(project, 0, 0)

    def set_normal(self, budget: SubsidiaryBudget) -> None:
        project = budget.volc_project
        self.restore_endpoint_limits(project)
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
            with SessionLocal.begin() as db:
                row = db.get(ControlledResource, resource.id)
                if row:
                    db.delete(row)

    def throttle(self, budget: SubsidiaryBudget) -> None:
        self.set_endpoint_limits(
            budget.volc_project,
            budget.throttle_concurrency,
            budget.throttle_rps * 60,
        )
