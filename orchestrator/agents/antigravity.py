"""Google Antigravity placeholder adapter."""

from orchestrator.agents.base import BaseAgent
from orchestrator.models import AgentContext, AgentResult


class AntigravityAgent(BaseAgent):
    name = "antigravity"
    role = "CTO, planner, and final decision maker"
    instructions = (
        "Turn the phase goal into an actionable technical plan, resolve tradeoffs, "
        "and approve or reject the reviewed implementation."
    )

    def execute(self, context: AgentContext) -> AgentResult:
        if context.step_id == "planning":
            content = (
                f"## Objective\n\n{context.phase_prompt}\n\n"
                "## Planning checklist\n\n"
                "- Confirm scope and acceptance criteria.\n"
                "- Review project memory and architectural constraints.\n"
                "- Break implementation into testable changes.\n"
                "- Record significant tradeoffs in decisions.md.\n\n"
                "## Integration status\n\n"
                "Placeholder handoff: connect the Antigravity CLI/API in this agent's execute method."
            )
            return AgentResult(self.name, context.step_id, "Antigravity implementation plan", content)

        content = (
            "## Final review checklist\n\n"
            "- Compare the implementation handoff with the original phase objective.\n"
            "- Resolve issues reported by Cursor.\n"
            "- Identify required improvements and final acceptance conditions.\n\n"
            f"## Inputs reviewed\n\n{self.prior_handoffs(context)}\n\n"
            "## Integration status\n\nPlaceholder final review; no external CLI/API was called."
        )
        return AgentResult(self.name, context.step_id, "Antigravity final review", content)
