import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("start", Path(__file__).parents[1] / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_ensure_env_creates_stable_token(tmp_path):
    (tmp_path / ".env.example").write_text(
        f"DRY_RUN=true\nCONFIG_API_TOKEN={start.PLACEHOLDER_TOKEN}\n",
        encoding="utf-8",
    )

    token, generated = start.ensure_env(tmp_path)
    assert generated
    assert token in (tmp_path / ".env").read_text(encoding="utf-8")

    same_token, generated = start.ensure_env(tmp_path)
    assert not generated
    assert same_token == token
