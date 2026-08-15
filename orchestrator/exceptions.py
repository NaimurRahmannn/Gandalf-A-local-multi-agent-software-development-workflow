"""Application-specific exceptions."""


class OrchestratorError(Exception):
    """Base class for expected orchestrator failures."""


class ConfigurationError(OrchestratorError):
    """Raised when configuration cannot be loaded or validated."""


class AgentExecutionError(OrchestratorError):
    """Raised when an agent cannot complete its assigned step."""


class WorkflowError(OrchestratorError):
    """Raised when a workflow cannot be completed."""
