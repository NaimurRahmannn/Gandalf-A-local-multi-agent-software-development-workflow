from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from orchestrator.agents import build_agents
from orchestrator.config import load_config
from orchestrator.core.cli_runner import CliRunner
from orchestrator.exceptions import ConfigurationError


class ConfigAndAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "runtime" / uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_loads_commands_arguments_timeouts_and_agents(self) -> None:
        config_path = self.root / "config.yaml"
        config_path.write_text(
            "paths:\n  memory_dir: memory\n  workspace_dir: work\n"
            "  phases_dir: runs\n  prompts_dir: prompts\n"
            "agents:\n  antigravity:\n    enabled: false\n    command: custom-agy\n"
            "    arguments: ['--print', '{prompt}']\n    timeout_seconds: 42\n"
            "  codex:\n    command: custom-codex\n"
            "  cursor:\n    command: custom-cursor\n",
            encoding="utf-8",
        )
        config = load_config(config_path, self.root)
        agents = build_agents(config, CliRunner())

        self.assertEqual(self.root / "memory" / "runs", config.paths.phases_dir)
        self.assertEqual(self.root / "prompts", config.paths.prompts_dir)
        self.assertFalse(config.agents["antigravity"].enabled)
        self.assertEqual("custom-agy", config.agents["antigravity"].command)
        self.assertEqual(42, config.agents["antigravity"].timeout_seconds)
        self.assertEqual(["antigravity", "codex", "cursor"], [agent.name for agent in agents])

    def test_rejects_invalid_timeout(self) -> None:
        config_path = self.root / "config.yaml"
        config_path.write_text(
            "agents:\n  codex:\n    timeout_seconds: 0\n", encoding="utf-8"
        )
        with self.assertRaises(ConfigurationError):
            load_config(config_path, self.root)
