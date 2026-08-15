# Local AI Development Team Orchestrator

A Python 3.11+ orchestrator that coordinates Google Antigravity, OpenAI Codex, and
Cursor as a local, Git-aware engineering team. It persists every phase, executes
project checks, loops on review feedback, and can resume interrupted work.

## Install

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Install and authenticate agent CLIs

Install each CLI from its vendor's current documentation because installers and
flags can change independently of this project.

- Google Antigravity: verify with `agy --version`, then launch `agy` once to complete
  Google OAuth and workspace trust. See the [Antigravity CLI codelab](https://codelabs.developers.google.com/antigravity-cli-hands-on).
- OpenAI Codex: install from the [official Codex CLI documentation](https://developers.openai.com/codex/cli/features), run `codex login`, then verify with
  `codex login status`. See [Codex authentication](https://developers.openai.com/codex/auth).
- Cursor: install from the [Cursor CLI guide](https://docs.cursor.com/en/cli/installation), then run `agent login` and `agent status`. Some installations use
  `cursor-agent`; update the configured command if needed. See [Cursor headless mode](https://docs.cursor.com/en/cli/headless).

The configured adapters use non-interactive modes. They never add Antigravity's
permission bypass, Codex's sandbox bypass, or Cursor's `--force` flag.

## Configure

Edit `orchestrator/config.yaml`. Paths are relative to the selected project root.

```yaml
workflow:
  max_review_cycles: 3
  require_approval: true

checks:
  commands:
    - pytest
    - npm test
  timeout_seconds: 600

git:
  allow_commit: false
  commit_message: "AI team: complete {phase_id}"
```

Agent configuration supports `enabled`, `command`, `arguments`, and
`timeout_seconds`. `{prompt}` inserts the rendered prompt as one argument. If the
argument list omits `{prompt}`, the prompt is sent on stdin.

`require_approval` means Antigravity must return `REVIEW_DECISION: APPROVED` for a
successful phase. If the cycle limit is reached with required issues, the phase ends
as `needs_attention`. `git.allow_commit` is a separate, default-off approval gate;
review approval never authorizes a commit.

Leave `checks.commands` empty when the workspace has no automated checks. Test
failures are recorded and given to reviewers; they do not hide the review output.
Commands are parsed as argument arrays, so shell pipelines and redirection are not
supported; call a checked-in script when a check needs shell composition.

## Run or resume a phase

Place the source repository in `workspace/`, populate `.ai-memory/*.md`, and run:

```powershell
python orchestrator/main.py "Build authentication system"
```

The explicit `phase` form also works:

```powershell
python orchestrator/main.py phase "Build authentication system"
```

Resume a failed or interrupted Phase 3 run without replaying completed steps:

```powershell
python orchestrator/main.py --resume <phase-id>
```

Other options are `--project-root PATH`, `--config PATH`, and `--verbose`.

## Complete AI workflow

```text
Load memory
  -> Antigravity plan
  -> pre-implementation Git checkpoint and backup
  -> Codex implementation
  -> collect status, changed files, and diff
  -> run configured tests
  -> Cursor code/QA review
  -> Antigravity architecture and acceptance decision
       -> APPROVED: finalize
       -> CHANGES_REQUIRED: Codex improvements -> tests -> review again
  -> after-state checkpoint
  -> optional explicitly approved commit
  -> final report and project-memory updates
```

Every agent prompt contains its role, the current phase, project context,
architecture, previous decisions, team rules, and prior handoffs. Cursor is asked to
review the actual working-tree diff. Antigravity decides whether implementation,
architecture, and tests meet the phase goal.

## Phase lifecycle and artifacts

Each phase is stored under `.ai-memory/phases/<phase-id>/`:

```text
prompt.md
plan.md
before-state.txt
changes.diff
implementation.md
review.md
improvements.md
after-state.txt
test-results.md
phase-report.md
status.json                 # resumable state and next action
handoffs.json               # context needed after restart
tasks/                      # ordered Phase 1-compatible handoffs
backups/                    # pre-Codex patches and untracked-file archives
logs/                       # workflow, Git, tests, prompts, stdout, stderr
```

Phase 2 artifact aliases such as `antigravity-plan.md` and `cursor-review.md` are
still generated for compatibility.

Terminal statuses:

- `completed`: Antigravity approved, or approval was disabled.
- `needs_attention`: the configured cycle limit ended without required approval.
- `failed`: an agent, command, configuration, Git operation, or finalization failed.
- `interrupted`: Ctrl+C stopped the active step.

Failed and interrupted Phase 3 runs retain `next_action`, completed handoffs, logs,
and snapshots for `--resume`.

## Git safety and commits

Before Codex runs, the orchestrator verifies the repository, records HEAD and status,
saves tracked changes as a binary patch, and archives untracked workspace files. It
never resets, checks out, deletes, rewrites history, pushes, or silently discards user
changes.

`GitManager.create_commit()` refuses to run unless `git.allow_commit: true`. When
enabled, it commits only the configured workspace path and uses the configured
message. Keep this setting false unless automatic local commits are explicitly wanted.

## Project memory

At finalization the orchestrator updates:

- `progress.md` with the terminal outcome.
- `decisions.md` with the review decision and report link.
- `architecture.md` with the outcome and changed-file record.

Updates are phase-keyed and idempotent so resuming finalization does not duplicate
the same phase entry.

## Troubleshooting

- **CLI not found:** run `<command> --version`, then update `agents.*.command`.
- **Authentication failure:** run `agy`, `codex login status`, or `agent status`.
- **Not a Git repository:** initialize Git in `workspace/` or place `workspace/`
  inside the intended repository. Codex will not start until this check passes.
- **Timeout:** inspect `logs/<step>-<agent>.log` and increase the relevant timeout.
- **Test failure:** inspect `test-results.md` and the corresponding test log.
- **Needs attention:** read `review.md` and `phase-report.md`; resolve or start a
  focused follow-up phase.
- **Interrupted/failed:** fix the external cause and run `--resume <phase-id>`.
- **Old phase cannot resume:** Phase 1/2 state files predate the resumable state
  machine; start a new Phase 3 run with the original prompt.

## Tests

Tests use deterministic local fake agents and temporary Git repositories, so they do
not consume model credits:

```powershell
python -m unittest discover -s tests -v
```

Coverage includes CLI output/failures/timeouts, configuration, real adapter loading,
Git status/diff/files/checkpoints/commit gating, test-result capture, iterative review,
approval limits, named snapshots, reports, memory updates, and failure recovery.
