# Local AI Development Team Orchestrator

This local Python 3.11+ application coordinates real command-line AI agents through
a durable five-stage development workflow:

```text
Antigravity plan -> Codex implementation -> Cursor review
                 -> Antigravity final decision -> Codex improvements
```

The orchestrator owns prompts, sequencing, Git safety snapshots, logs, and artifacts.
The external CLIs own model access and authentication.

## Install the orchestrator

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Install and authenticate agent CLIs

Install each CLI from its vendor's current documentation; CLI installers and flags
can change independently of this project.

### Google Antigravity CLI

Install Antigravity CLI and verify it with:

```text
agy --version
agy
```

The first interactive launch guides you through Google OAuth and workspace trust.
The adapter uses documented plan and print modes (`agy --mode plan -p ...`) and never enables
`--dangerously-skip-permissions`. See the [Google Antigravity CLI codelab](https://codelabs.developers.google.com/antigravity-cli-hands-on).

### OpenAI Codex CLI

Install Codex using an option in the [official Codex CLI documentation](https://developers.openai.com/codex/cli/features), then authenticate:

```text
codex login
codex login status
```

Codex supports ChatGPT sign-in and API-key sign-in. The adapter uses
`codex exec --sandbox workspace-write --color never -`, sends the prompt on stdin,
and does not bypass approvals or sandboxing. See the [Codex authentication guide](https://developers.openai.com/codex/auth).

### Cursor CLI

Install Cursor CLI from the [Cursor CLI installation guide](https://docs.cursor.com/en/cli/installation), then authenticate using its browser login or a
`CURSOR_API_KEY` as documented by Cursor:

```text
agent login
agent status
```

Some installations expose `cursor-agent` instead of `agent`; set the configured
command accordingly. The adapter uses non-interactive print mode and does not pass
`--force`. See [Cursor headless mode](https://docs.cursor.com/en/cli/headless).

## Configure

Commands, argument arrays, and timeouts live in `orchestrator/config.yaml`:

```yaml
agents:
  antigravity:
    enabled: true
    command: "agy"
    arguments: ["--mode", "plan", "-p", "{prompt}"]
    timeout_seconds: 600
  codex:
    enabled: true
    command: "codex"
    arguments: ["exec", "--sandbox", "workspace-write", "--color", "never", "-"]
    timeout_seconds: 1200
  cursor:
    enabled: true
    command: "agent"
    arguments: ["-p", "--output-format", "text", "{prompt}"]
    timeout_seconds: 600
```

`{prompt}` passes the rendered prompt as one argument. When an argument list omits
`{prompt}`, the orchestrator sends the prompt on stdin. Commands are parsed into
argument arrays and executed without `shell=True`. Paths are resolved relative to
the selected project root.

## Run a phase

Place the source project in `workspace/`, populate the Markdown files in
`.ai-memory/`, and run:

```powershell
python orchestrator/main.py "Build authentication system"
```

The explicit subcommand-like form is also accepted:

```powershell
python orchestrator/main.py phase "Build authentication system"
```

Options:

```text
--project-root PATH   Project containing workspace/ and .ai-memory/
--config PATH         Config path (default: orchestrator/config.yaml)
--verbose             Enable debug console logging
```

## Prompt and handoff flow

The orchestrator renders templates from `orchestrator/prompts/`. Every prompt includes
the agent role, current phase, project context, architecture, previous decisions,
team rules, and prior agent handoffs.

Each successful phase writes:

```text
.ai-memory/phases/<phase-id>/
|-- prompt.md
|-- status.json
|-- antigravity-plan.md
|-- codex-report.md
|-- cursor-review.md
|-- antigravity-final-review.md
|-- codex-improvement-report.md
|-- tasks/                     # Phase 1-compatible ordered handoffs
|-- backups/
|   |-- implementation/
|   `-- improvement/
`-- logs/
    |-- workflow.log
    |-- git-safety.log
    |-- <step>.prompt.txt
    `-- <step>-<agent>.log
```

`status.json` is the machine-readable source of truth. A CLI failure or timeout marks
the current step and phase as failed. Ctrl+C marks the phase interrupted.

## Git safety

Before either Codex step, the orchestrator:

1. Verifies that `workspace/` is inside a Git repository.
2. Records the current HEAD and scoped `git status`.
3. Saves tracked changes as a binary Git patch.
4. Archives untracked workspace files in a ZIP file.

The snapshot is additive and never resets, checks out, deletes, commits, or pushes.
Codex is explicitly instructed not to delete files or discard user changes. If the
Git check or backup fails, Codex is not started.

## Tests

The test suite uses deterministic local fake agents, so it does not require live
accounts or consume model credits:

```powershell
python -m unittest discover -s tests -v
```

It covers subprocess output, errors and timeouts; configuration validation; adapter
construction; Git backups; five-stage workflow execution; named artifacts; and
persisted failure state.
