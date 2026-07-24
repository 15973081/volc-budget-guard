import os
from pathlib import Path
import re
import secrets
import sys

PLACEHOLDER_TOKEN = "replace-with-a-long-random-token"


def ensure_env(root: Path) -> tuple[str, bool]:
    env_path = root / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    else:
        example = root / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ""

    match = re.search(r"(?m)^CONFIG_API_TOKEN=(.*)$", content)
    current = match.group(1).strip() if match else ""
    if current and current != PLACEHOLDER_TOKEN:
        return current, False

    token = secrets.token_urlsafe(32)
    if match:
        content = content[:match.start(1)] + token + content[match.end(1):]
    else:
        content = content.rstrip() + f"\nCONFIG_API_TOKEN={token}\n"
    env_path.write_text(content, encoding="utf-8")
    return token, True


def main() -> None:
    root = Path(__file__).resolve().parent
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))
    token, generated = ensure_env(root)

    try:
        import uvicorn
        from budget_guard.config import settings
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少依赖，请先运行: python -m pip install -e .") from exc

    print("\nBudget Guard 启动中")
    print(f"配置页面: http://127.0.0.1:{settings.app_port}/admin")
    if generated:
        print(f"首次访问令牌: {token}")
    else:
        print("访问令牌: 使用 .env 中的 CONFIG_API_TOKEN")
    print("停止服务: Ctrl+C\n")
    uvicorn.run("budget_guard.api.main:app", host="127.0.0.1", port=settings.app_port)


if __name__ == "__main__":
    main()
