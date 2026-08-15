"""Agent implementations bundled with the orchestrator."""

from orchestrator.agents.antigravity import AntigravityAgent
from orchestrator.agents.codex import CodexAgent
from orchestrator.agents.cursor import CursorAgent
from orchestrator.agents.base import BaseAgent
from orchestrator.config import AppConfig
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_safety import GitSafety


def build_agents(config: AppConfig, runner: CliRunner) -> list[BaseAgent]:
    """Create configured adapters while sharing execution and safety services."""

    git_safety = GitSafety(runner)
    return [
        AntigravityAgent(config.agents["antigravity"], runner, config.paths.prompts_dir),
        CodexAgent(
            config.agents["codex"],
            runner,
            config.paths.prompts_dir,
            git_safety=git_safety,
        ),
        CursorAgent(config.agents["cursor"], runner, config.paths.prompts_dir),
    ]


__all__ = ["AntigravityAgent", "CodexAgent", "CursorAgent", "build_agents"]
