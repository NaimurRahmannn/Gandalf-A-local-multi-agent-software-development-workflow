# Local AI Development Team Orchestrator

A Python 3.11+ orchestrator that coordinates Google Antigravity, OpenAI Codex, and
Cursor as a local, Git-aware engineering team. It includes both the original CLI and
a password-protected local dashboard for managing projects, watching live progress,
reviewing artifacts, and making durable approval decisions.

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

The CLI remains autonomous and backward compatible: it uses the same workflow but
does not pause for dashboard decisions. Antigravity review and `git.allow_commit`
continue to control its acceptance and commit behavior.

## Start the local dashboard

Set a password in the process environment, then start the FastAPI application:

```powershell
$env:AI_TEAM_DASHBOARD_PASSWORD = "use-a-long-local-password"
python -m dashboard.backend.main
```

Open `http://127.0.0.1:8000` and sign in as `admin` with that password. Host, port,
worker count, username, database location, projects root, and frontend location are
configured in `dashboard/config.yaml`. The server binds only to loopback by default;
do not expose it to a network without adding TLS and stronger authentication.

From the dashboard:

1. Open **Projects**, create a project, and verify its root path.
2. Open the project and submit a phase goal.
3. Follow status, agent activity, artifacts, tests, and command logs on the phase page.
4. At an approval card, inspect `changes.diff`, `review.md`, `test-results.md`, and
   the Antigravity review before choosing **Approve**, **Request changes**, or
   **Reject**.
5. If commits are enabled, make the second independent decision at the Git commit
   gate.

Each dashboard project lives under `workspace/<project-slug>/` and has its own Git
repository and `.ai-memory/` tree. The dashboard database at
`dashboard/data/dashboard.db` is an index and event ledger; project artifacts remain
the durable source of truth. `.ai-memory/` is added to the repository-local Git
exclude file so execution logs and orchestration state are not included in source
diffs or approved commits.

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
  -> dashboard human approval gate
       -> APPROVE: continue
       -> REQUEST CHANGES: Codex improvements -> tests -> review again
       -> REJECT: fail with the decision preserved
  -> after-state checkpoint
  -> optional independent human approval gate for a configured Git commit
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
phase-status.json           # dashboard-facing status and current agent
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

Dashboard phases use the visual states `CREATED`, `PLANNING`, `CODING`, `REVIEWING`,
`IMPROVING`, `TESTING`, `WAITING_APPROVAL`, `COMPLETED`, and `FAILED`. Every
transition is stored in SQLite and streamed to the phase page with Server-Sent
Events. A server restart recovers unfinished work; phases waiting on a human remain
paused without holding a worker thread.

## API

All routes require local HTTP Basic authentication. The frontend uses these same
endpoints:

- `GET/POST /projects` and `GET /projects/{id}`
- `GET /phases`, `POST /phases/start`, and `GET /phases/{id}`
- `POST /phases/{id}/approve`, `/reject`, or `/request-changes`
- `GET /agents/status`, `GET /logs/{phase_id}`, and `GET /events/{phase_id}`
- `GET /notifications` and `POST /notifications/{id}/read`

The events route is an SSE stream. `after_id` resumes after a known event and
`once=true` performs a bounded read useful for diagnostics and tests.

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
- **Dashboard will not start:** set `AI_TEAM_DASHBOARD_PASSWORD` in the same shell
  that launches Python and confirm `dashboard/config.yaml` paths exist.
- **Dashboard shows an agent as missing:** install that CLI or update its configured
  command; `/agents/status` checks the executable currently on `PATH`.
- **Approval appears stuck:** refresh the phase page, verify the latest approval is
  pending, and inspect `workflow.log`; decisions are durable and a successful
  decision schedules resume automatically.
- **No live updates:** confirm the page can reach `/events/<phase-id>` and that no
  reverse proxy is buffering SSE. Manual refresh reads the same stored event ledger.
- **Database recovery:** stop the server before copying the SQLite database and its
  WAL files. Project artifacts can still be inspected directly under `.ai-memory/`.
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
named snapshots, reports, memory updates, failure recovery, authenticated APIs,
SQLite durability, SSE events, notification state, and both human approval gates.
