"""Cursor CLI adapter."""

from orchestrator.agents.base import ExternalCliAgent
from orchestrator.models import AgentContext


class CursorAgent(ExternalCliAgent):
    name = "cursor"
    role = "QA reviewer and code reviewer"
    instructions = (
        "Review changes for correctness, regressions, security, maintainability, "
        "and test coverage; report findings by severity."
    )

    def template_name(self, context: AgentContext) -> str:
        return "cursor_reviewer.txt"

    def result_summary(self, context: AgentContext) -> str:
        return "Cursor QA and code review"
