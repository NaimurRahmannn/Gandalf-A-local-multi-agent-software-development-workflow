"""Infrastructure used by agent adapters."""

from orchestrator.core.cli_runner import CliResult, CliRunner
from orchestrator.core.git_safety import GitBackup, GitSafety
from orchestrator.core.git_manager import GitCheckpoint, GitManager
from orchestrator.core.test_runner import TestRunner, TestSuiteResult
from orchestrator.core.workflow_observer import (
    ApprovalResolution,
    NullWorkflowObserver,
    WorkflowObserver,
)

__all__ = [
    "CliResult",
    "CliRunner",
    "GitBackup",
    "GitSafety",
    "GitCheckpoint",
    "GitManager",
    "TestRunner",
    "TestSuiteResult",
    "ApprovalResolution",
    "NullWorkflowObserver",
    "WorkflowObserver",
]
