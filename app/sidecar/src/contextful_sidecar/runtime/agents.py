"""Load and compose layered agent instruction documents from agents/."""
from __future__ import annotations

from pathlib import Path

AGENTS_DIR = "agents"

# Built-in fallbacks when agents/ is missing (older worktrees, minimal smoke fixtures).
_FALLBACKS: dict[str, str] = {
    "workspace-orchestrator": (
        "# Workspace Orchestrator (fallback)\n\n"
        "Be evidence-driven and concise. Cite repo paths as repos/<name>/<path>:line. "
        "Never write to repos/. Research files under research/ are not repo source. "
        "Stay within your assigned role.\n"
    ),
    "project-orchestrator": (
        "# Project Orchestrator (fallback)\n\n"
        "You coordinate Q&A for this project. Answer only from the provided context. "
        "Be concise. Do not invent run results.\n"
    ),
    "module-agent": (
        "# Module Agent (fallback)\n\n"
        "Run one module analysis. Use tools to inspect repos, then write_analysis and "
        "write_tasks. Read templates/ first. Stop after outputs are written.\n"
    ),
}


def load_agent_doc(workspace: Path, name: str) -> str:
    """Read agents/<name>.md from the workspace, or return a built-in fallback."""
    path = Path(workspace) / AGENTS_DIR / f"{name}.md"
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    return _FALLBACKS.get(name, f"# {name}\n\n(No instruction document found.)\n")


def compose_module_prompt(
    workspace: Path,
    runtime_ctx: str,
    skill_text: str,
) -> str:
    """Layer: workspace policy + module-agent role + runtime context + SKILL.md."""
    ws_doc = load_agent_doc(workspace, "workspace-orchestrator")
    mod_doc = load_agent_doc(workspace, "module-agent")
    return (
        f"=== WORKSPACE ORCHESTRATOR (global policy) ===\n{ws_doc}\n\n"
        f"=== MODULE AGENT (role) ===\n{mod_doc}\n\n"
        f"=== RUNTIME CONTEXT ===\n{runtime_ctx}\n\n"
        f"=== MODULE INSTRUCTIONS (SKILL.md) ===\n{skill_text}\n"
    )


def compose_orchestrator_prompt(workspace: Path, project_ctx: str) -> str:
    """Layer: workspace policy + project-orchestrator role + project context."""
    ws_doc = load_agent_doc(workspace, "workspace-orchestrator")
    proj_doc = load_agent_doc(workspace, "project-orchestrator")
    return (
        f"=== WORKSPACE ORCHESTRATOR (global policy) ===\n{ws_doc}\n\n"
        f"=== PROJECT ORCHESTRATOR (role) ===\n{proj_doc}\n\n"
        f"=== PROJECT CONTEXT ===\n{project_ctx}\n"
    )
