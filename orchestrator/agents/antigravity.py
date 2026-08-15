"""Google Antigravity CLI adapter."""

from orchestrator.agents.base import ExternalCliAgent
from orchestrator.models import AgentContext


class AntigravityAgent(ExternalCliAgent):
    name = "antigravity"
    role = "CTO, planner, and final decision maker"
    instructions = (
        "Turn the phase goal into an actionable technical plan, resolve tradeoffs, "
        "and approve or reject the reviewed implementation."
    )

    def template_name(self, context: AgentContext) -> str:
        return (
            "antigravity_planner.txt"
            if context.step_id == "planning"
            else "antigravity_final_review.txt"
        )

    def result_summary(self, context: AgentContext) -> str:
        return (
            "Antigravity implementation plan"
            if context.step_id == "planning"
            else "Antigravity final review"
        )
