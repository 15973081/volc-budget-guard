from abc import ABC, abstractmethod
import httpx

class Limiter(ABC):
    @abstractmethod
    def set_normal(self, project_id: str) -> None: ...
    @abstractmethod
    def throttle(self, project_id: str, rps: int) -> None: ...
    @abstractmethod
    def block(self, project_id: str) -> None: ...

class LogLimiter(Limiter):
    def set_normal(self, project_id: str) -> None: print(f"NORMAL {project_id}")
    def throttle(self, project_id: str, rps: int) -> None: print(f"THROTTLE {project_id} rps={rps}")
    def block(self, project_id: str) -> None: print(f"BLOCK {project_id}")

class WebhookLimiter(Limiter):
    """Calls your gateway/control-plane, not Volcengine resources directly."""
    def __init__(self, url_template: str, token: str, dry_run: bool = True):
        self.url_template, self.token, self.dry_run = url_template, token, dry_run

    def _apply(self, project_id: str, state: str, rps: int | None = None) -> None:
        if self.dry_run:
            print(f"DRY_RUN limiter project={project_id} state={state} rps={rps}")
            return
        url = self.url_template.format(project_id=project_id)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = httpx.put(url, json={"state": state, "rps": rps}, headers=headers, timeout=10)
        response.raise_for_status()

    def set_normal(self, project_id: str) -> None: self._apply(project_id, "normal")
    def throttle(self, project_id: str, rps: int) -> None: self._apply(project_id, "throttled", rps)
    def block(self, project_id: str) -> None: self._apply(project_id, "blocked", 0)
