"""Application-specific exceptions."""


class OrchestratorError(Exception):
    """Base class for expected orchestrator failures."""


class ConfigurationError(OrchestratorError):
    """Raised when configuration cannot be loaded or validated."""


class AgentExecutionError(OrchestratorError):
    """Raised when an agent cannot complete its assigned step."""


class WorkflowError(OrchestratorError):
    """Raised when a workflow cannot be completed."""


class CliRunnerError(OrchestratorError):
    """Base class for external command failures."""


class CliNotFoundError(CliRunnerError):
    """Raised when a configured executable cannot be found."""


class CliTimeoutError(CliRunnerError):
    """Raised when an external command exceeds its timeout."""


class CliExecutionError(CliRunnerError):
    """Raised when an external command exits unsuccessfully."""


class GitSafetyError(OrchestratorError):
    """Raised when the pre-modification Git safety check fails."""


class PromptTemplateError(OrchestratorError):
    """Raised when an agent prompt template is missing or invalid."""
