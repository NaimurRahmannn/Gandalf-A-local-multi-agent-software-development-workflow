# Local AI Development Team Orchestrator

Phase 1 provides a durable, local workflow foundation for coordinating three AI
development roles. The bundled agents are deterministic placeholders: they create
structured handoff files but do not call external APIs or CLIs yet.

## Requirements and installation

- Python 3.11 or newer

From the repository root:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate with `source venv/bin/activate` instead.

## Run a phase

```powershell
python orchestrator/main.py "Build authentication system"
```

The explicit `phase` form is also accepted:

```powershell
python orchestrator/main.py phase "Build authentication system"
```

Useful options:

```text
--project-root PATH   Project containing workspace/ and .ai-memory/
--config PATH         Config path (default: orchestrator/config.yaml)
--verbose             Enable console debug logging
```

## Workflow

Each enabled agent receives the original prompt, shared project memory, and all
earlier handoffs in the phase:

```text
Antigravity planner -> Codex developer -> Cursor reviewer
                    -> Antigravity final review -> Codex improvement
```

Every step is persisted before the next starts. A failed agent marks the phase as
`failed`; Ctrl+C marks it as `interrupted`. This makes incomplete work visible and
keeps its diagnostic log.

## Folder structure

```text
AI-Team/
|-- orchestrator/
|   |-- main.py              # CLI entry point
|   |-- config.py            # YAML loading and validation
|   |-- memory.py            # Shared-memory and phase persistence
|   |-- models.py            # Typed workflow contracts
|   |-- workflow.py          # Workflow coordinator
|   |-- config.yaml
|   `-- agents/
|       |-- base.py          # Agent interface
|       |-- antigravity.py
|       |-- codex.py
|       `-- cursor.py
|-- workspace/               # Source project worked on by future integrations
|-- .ai-memory/
|   |-- project.md
|   |-- architecture.md
|   |-- decisions.md
|   |-- progress.md
|   |-- team-rules.md
|   |-- reviews/
|   `-- phases/              # Generated, ignored by Git by default
`-- tests/
```

A generated phase contains `prompt.md`, `status.json`, `tasks/*.md`, and
`logs/workflow.log`. The JSON file is the machine-readable source of truth; the
Markdown files are human-readable agent handoffs.

## Configuration

Paths are relative to `project_root`, not the shell's current directory. Agents
can be disabled independently in `orchestrator/config.yaml`. Disabling one skips
all workflow steps assigned to it.

## Adding real integrations

Keep orchestration and persistence unchanged. Implement the relevant agent's
`execute(context)` method (or introduce another `BaseAgent` implementation) and
return an `AgentResult`. Integration failures should raise `AgentExecutionError`
with the original exception chained.

## Tests

```powershell
python -m unittest discover -s tests -v
```
