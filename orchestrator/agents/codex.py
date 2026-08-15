"""Codex placeholder adapter."""

from orchestrator.agents.base import BaseAgent
from orchestrator.models import AgentContext, AgentResult


class CodexAgent(BaseAgent):
    name = "codex"
    role = "Senior developer and main coder"
    instructions = (
        "Implement approved plans in the workspace, test the changes, and address "
        "review feedback without silently changing scope."
    )

    def execute(self, context: AgentContext) -> AgentResult:
        improving = context.step_id == "improvement"
        action = "Improvement" if improving else "Implementation"
        content = (
            f"## {action} checklist\n\n"
            "- Inspect the current workspace before editing.\n"
            "- Implement the approved scope with typed, maintainable code.\n"
            "- Run focused tests and report failures honestly.\n"
            "- Update relevant project memory and documentation.\n\n"
            f"## Prior handoffs\n\n{self.prior_handoffs(context)}\n\n"
            "## Integration status\n\n"
            "Placeholder handoff: connect the Codex CLI/API in this agent's execute method."
        )
        return AgentResult(self.name, context.step_id, f"Codex {action.lower()} handoff", content)
