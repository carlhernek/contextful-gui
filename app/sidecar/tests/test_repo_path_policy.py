"""Repo path policy: block secrets and gitignored paths under repos/."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from contextful_sidecar.runtime.repo_path_policy import (
    check_repo_path,
    is_gitignored_repo_path,
    is_sensitive_repo_path,
)
from contextful_sidecar.runtime.tools import execute_tool


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_sensitive_basenames_blocked():
    assert is_sensitive_repo_path(Path(".env"))
    assert is_sensitive_repo_path(Path("config/.env.local"))
    assert is_sensitive_repo_path(Path("deploy/server.pem"))
    assert is_sensitive_repo_path(Path(".ssh/id_rsa"))
    assert not is_sensitive_repo_path(Path("src/main.ts"))


def test_gitignored_path_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        repo = ws / "repos" / "app"
        repo.mkdir(parents=True)
        _git_init(repo)
        (repo / ".gitignore").write_text("secrets/\n*.local\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "main.ts").write_text("export {}\n", encoding="utf-8")
        (repo / "secrets").mkdir()
        (repo / "secrets" / "token.txt").write_text("sk-live-secret\n", encoding="utf-8")

        assert check_repo_path(ws, repo / "src" / "main.ts") is None
        assert check_repo_path(ws, repo / "secrets" / "token.txt") is not None
        assert is_gitignored_repo_path(repo, Path("secrets/token.txt"))


def test_read_file_blocks_env_and_gitignored():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        repo = ws / "repos" / "web"
        repo.mkdir(parents=True)
        _git_init(repo)
        (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        (repo / ".env").write_text("API_KEY=supersecret\n", encoding="utf-8")
        (repo / "README.md").write_text("# App\n", encoding="utf-8")

        env_out = execute_tool(ws, "read_file", {"path": "repos/web/.env"})
        assert env_out.startswith("ERROR:")
        assert "blocked" in env_out.lower()

        readme_out = execute_tool(ws, "read_file", {"path": "repos/web/README.md"})
        assert "# App" in readme_out


def test_list_directory_hides_blocked_entries():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        repo = ws / "repos" / "web"
        repo.mkdir(parents=True)
        (repo / ".env").write_text("x=1\n", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "index.ts").write_text("export {}\n", encoding="utf-8")

        out = execute_tool(ws, "list_directory", {"path": "repos/web"})
        assert ".env" not in out
        assert "src" in out


def test_gather_context_skips_env():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        repo = ws / "repos" / "web"
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("# Web\n", encoding="utf-8")
        (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

        out = execute_tool(ws, "gather_context", {"path": "repos/web"})
        assert "Web" in out
        assert "SECRET=1" not in out
        assert "supersecret" not in out
