"""Base contract implemented by every agent integration."""

from __future__ import annotations

from abc import ABC, abstractmethod

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
