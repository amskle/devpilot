# Repository Guidelines

## Project Structure & Module Organization

`devpilot/` contains the active LangGraph runtime, CLI, domain models, services, and tool executor. `skills/` contains deterministic skills with their executors, metadata, instructions, and tests. Integration tests live in `tests/`. Architecture decisions and contracts are under `docs/`; `demo/` provides sample scenarios. `runtime/` is a compatibility layer. Treat `agentteams/` and `mcp/` as legacy material unless a change explicitly targets them.

## Build, Test, and Development Commands

Create an environment and install the locked dependencies plus the editable package:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps -e .
```

Run CI's suite with `python -m pytest skills tests -q`. For focused work, use `python -m pytest tests/test_phase1_graph.py -q`. Inspect CLI commands with `python -m devpilot --help`. An end-to-end run requires a clean Git repository: `python -m devpilot task create --repo C:\path\to\repo --request "fix failing tests"`.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation and type annotations on public functions. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes and Pydantic models; and uppercase names for constants. Skill directories use `kebab-case` and must include `metadata.yaml`, `executor.py`, `SKILL.md`, and `tests/test_executor.py`. Keep state transitions deterministic and validate external data at Pydantic boundaries. No formatter is enforced, so preserve the surrounding style and keep diffs focused.

## Testing Guidelines

Pytest discovers `test_*.py` files and `test_*` functions. Add unit tests beside skills and integration/state-machine tests under `tests/`. Cover success, failure, budget, retry, approval, and recovery paths when changing orchestration. Use the scripted fake gateway and temporary Git repositories. Run the full suite before submitting.

## Commit & Pull Request Guidelines

Use Conventional Commits such as `feat:`, `fix:`, `docs:`, `test:`, or `refactor:`; keep the subject concise and describe one logical change. Branches should follow `feat/<topic>` or `fix/<topic>`. Pull requests must explain motivation, behavior changes, tests run, and any ADR or documentation impact. Link relevant issues and include screenshots only for user-visible UI changes. CI must pass on Python 3.10 and 3.13.

## Security & Configuration

Configure model access through `DEVPILOT_MODEL_API_KEY`, `DEVPILOT_MODEL_BASE_URL`, and `DEVPILOT_MODEL`; never commit credentials or generated workspace data. Preserve workspace ID, lease, revision, path-boundary, approval, and budget checks. DevPilot modifies isolated worktrees, not the supplied source checkout.
