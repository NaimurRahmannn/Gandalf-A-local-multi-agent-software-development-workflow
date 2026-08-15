"""Codex CLI adapter with mandatory Git safety snapshots."""

from pathlib import Path

from orchestrator.agents.base import ExternalCliAgent
from orchestrator.config import AgentConfig
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_safety import GitSafety
from orchestrator.models import AgentContext, AgentResult


class CodexAgent(ExternalCliAgent):
    name = "codex"
    role = "Senior developer and main coder"
    instructions = (
        "Implement approved plans in the workspace, test the changes, and address "
        "review feedback without silently changing scope."
    )

    def __init__(
        self,
        config: AgentConfig,
        runner: CliRunner,
        prompts_dir: Path,
        *,
        git_safety: GitSafety,
    ) -> None:
        super().__init__(config, runner, prompts_dir)
        self.git_safety = git_safety

    def template_name(self, context: AgentContext) -> str:
        return "codex_developer.txt"

    def result_summary(self, context: AgentContext) -> str:
        action = "improvement" if context.step_id == "improvement" else "implementation"
        return f"Codex {action} report"

    def execute(self, context: AgentContext) -> AgentResult:
        backup = self.git_safety.create_backup(
            context.workspace_dir, context.phase_dir, context.step_id
        )
        prompt = self.build_prompt(context)
        result = self.run_cli(context, prompt)
        status_after = self.git_safety.status(
            context.workspace_dir, context.phase_dir / "logs" / "git-safety.log"
        )
        content = result.stdout.strip() or "CLI completed successfully without textual output."
        content += f"\n\n## Git status after Codex\n\n```text\n{status_after or 'Clean'}\n```"
        metadata = self.result_metadata(result, context)
        metadata.update(
            {
                "git_head_before": backup.head or "unborn HEAD",
                "git_backup": str(backup.backup_dir.relative_to(context.phase_dir)),
            }
        )
        return AgentResult(
            self.name,
            context.step_id,
            self.result_summary(context),
            content,
            metadata,
        )
