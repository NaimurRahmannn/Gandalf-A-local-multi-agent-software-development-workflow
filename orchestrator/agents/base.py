"""Base contract implemented by every agent integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from orchestrator.config import AgentConfig
from orchestrator.core.cli_runner import CliResult, CliRunner
from orchestrator.exceptions import PromptTemplateError
from orchestrator.models import AgentContext, AgentResult


class BaseAgent(ABC):
    name: str
    role: str
    instructions: str

    @abstractmethod
    def execute(self, context: AgentContext) -> AgentResult:
        """Execute one workflow step and return its durable handoff."""

    @staticmethod
    def prior_handoffs(context: AgentContext) -> str:
        if not context.prior_results:
            return "No prior handoffs."
        return "\n\n".join(
            f"### {item.agent_name}: {item.summary}\n{item.content}"
            for item in context.prior_results
        )


class ExternalCliAgent(BaseAgent):
    """Shared mechanics for a real, non-interactive CLI-backed agent."""

    def __init__(
        self,
        config: AgentConfig,
        runner: CliRunner,
        prompts_dir: Path,
    ) -> None:
        self.config = config
        self.runner = runner
        self.prompts_dir = prompts_dir

    @abstractmethod
    def template_name(self, context: AgentContext) -> str:
        """Select the prompt template used for this workflow step."""

    @abstractmethod
    def result_summary(self, context: AgentContext) -> str:
        """Return the human-readable result title."""

    def execute(self, context: AgentContext) -> AgentResult:
        prompt = self.build_prompt(context)
        result = self.run_cli(context, prompt)
        content = result.stdout.strip() or "CLI completed successfully without textual output."
        return AgentResult(
            self.name,
            context.step_id,
            self.result_summary(context),
            content,
            self.result_metadata(result, context),
        )

    def build_prompt(self, context: AgentContext) -> str:
        template_path = self.prompts_dir / self.template_name(context)
        if not template_path.is_file():
            raise PromptTemplateError(f"Prompt template not found: {template_path}")
        template = template_path.read_text(encoding="utf-8")
        values = {
            "agent_name": self.name,
            "agent_role": self.role,
            "agent_instructions": self.instructions,
            "project_context": context.memory.get("project.md", ""),
            "architecture": context.memory.get("architecture.md", ""),
            "previous_decisions": context.memory.get("decisions.md", ""),
            "team_rules": context.memory.get("team-rules.md", ""),
            "current_phase": context.phase_prompt,
            "objective": context.objective,
            "prior_handoffs": self.prior_handoffs(context),
            "workspace_dir": str(context.workspace_dir),
        }
        try:
            return template.format_map(values).strip() + "\n"
        except KeyError as exc:
            raise PromptTemplateError(
                f"Unknown placeholder {exc} in prompt template {template_path}"
            ) from exc

    def run_cli(self, context: AgentContext, prompt: str) -> CliResult:
        (context.phase_dir / "logs" / f"{context.step_id}.prompt.txt").write_text(
            prompt, encoding="utf-8"
        )
        command = list(self.runner.split_command(self.config.command))
        prompt_in_arguments = False
        for argument in self.config.arguments:
            if "{prompt}" in argument:
                prompt_in_arguments = True
                command.append(argument.replace("{prompt}", prompt))
            else:
                command.append(argument)
        return self.runner.run(
            command,
            cwd=context.workspace_dir,
            stdin=None if prompt_in_arguments else prompt,
            log_path=context.phase_dir / "logs" / f"{context.step_id}-{self.name}.log",
            timeout_seconds=self.config.timeout_seconds,
            redact_values=(prompt,),
        )

    def result_metadata(self, result: CliResult, context: AgentContext) -> dict[str, str]:
        return {
            "command": self.config.command,
            "duration_seconds": f"{result.duration_seconds:.3f}",
            "execution_log": str(result.log_path.relative_to(context.phase_dir)),
        }
