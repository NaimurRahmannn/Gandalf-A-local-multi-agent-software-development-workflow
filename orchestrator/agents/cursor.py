"""Cursor placeholder adapter."""

from orchestrator.agents.base import BaseAgent
from orchestrator.models import AgentContext, AgentResult


class CursorAgent(BaseAgent):
    name = "cursor"
    role = "QA reviewer and code reviewer"
    instructions = (
        "Review changes for correctness, regressions, security, maintainability, "
        "and test coverage; report findings by severity."
    )

    def execute(self, context: AgentContext) -> AgentResult:
        content = (
            "## Review checklist\n\n"
            "- Validate behavior against the plan and phase prompt.\n"
            "- Review correctness, edge cases, security, and maintainability.\n"
            "- Run or inspect tests and identify missing coverage.\n"
            "- Classify findings as blocking, important, or suggestion.\n\n"
            f"## Inputs reviewed\n\n{self.prior_handoffs(context)}\n\n"
            "## Integration status\n\n"
            "Placeholder handoff: connect the Cursor CLI/API in this agent's execute method."
        )
        return AgentResult(self.name, context.step_id, "Cursor QA and code review", content)
