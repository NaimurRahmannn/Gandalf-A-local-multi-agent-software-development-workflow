"""Command-line entry point for the local AI team orchestrator."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Direct script execution places orchestrator/ rather than its parent on sys.path.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.agents import build_agents
from orchestrator.config import AppConfig, load_config
from orchestrator.core.cli_runner import CliRunner
from orchestrator.core.git_manager import GitManager
from orchestrator.core.test_runner import TestRunner
from orchestrator.exceptions import OrchestratorError
from orchestrator.memory import MemoryStore
from orchestrator.workflow import WorkflowManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a development phase through the local AI team workflow."
    )
    parser.add_argument("prompt", nargs="*", help="Phase prompt (an optional leading 'phase' is accepted)")
    parser.add_argument("--resume", metavar="PHASE_ID", help="Resume an interrupted or failed Phase 3 run")
    parser.add_argument("--project-root", type=Path, help="Project root; defaults to main.py's parent project")
    parser.add_argument("--config", type=Path, help="YAML config path, relative to the project root")
    parser.add_argument("--verbose", action="store_true", help="Enable debug console logging")
    return parser


def configure_logging(config: AppConfig, verbose: bool) -> None:
    level = logging.DEBUG if verbose else getattr(logging, config.log_level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[console_handler],
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    script_project_root = Path(__file__).resolve().parent.parent
    project_root = (args.project_root or script_project_root).resolve()
    config_path = args.config or Path("orchestrator/config.yaml")
    if not config_path.is_absolute():
        config_path = project_root / config_path

    try:
        config = load_config(config_path, project_root)
        configure_logging(config, args.verbose)
        if args.resume and args.prompt:
            raise OrchestratorError("Do not provide a new prompt when using --resume.")
        if not args.resume and not args.prompt:
            raise OrchestratorError("Provide a phase prompt or use --resume PHASE_ID.")
        prompt_parts = args.prompt[1:] if len(args.prompt) > 1 and args.prompt[0].lower() == "phase" else args.prompt
        prompt = " ".join(prompt_parts).strip()
        store = MemoryStore(config.paths.memory_dir, config.paths.phases_dir)
        runner = CliRunner()
        manager = WorkflowManager(
            config,
            build_agents(config, runner),
            store,
            GitManager(runner, allow_commit=config.git.allow_commit),
            TestRunner(runner, config.checks.commands, config.checks.timeout_seconds),
        )
        phase_dir = manager.resume(args.resume) if args.resume else manager.run(prompt)
        state = json.loads((phase_dir / "status.json").read_text(encoding="utf-8"))
        logging.getLogger(__name__).info("Phase artifacts: %s", phase_dir)
        print(f"Phase finished with status '{state['status']}': {phase_dir}")
        return 0 if state["status"] == "completed" else 2
    except KeyboardInterrupt:
        print("Workflow interrupted.", file=sys.stderr)
        return 130
    except (OrchestratorError, OSError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
