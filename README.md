# DevPilot CLI

A local-first Rust CLI and Python agent service for exploring software projects. It provides bounded workspace read/search tools, diff previews with explicit approval before edits, and Docker-isolated project command execution with timeouts and retries.

## Commands

Run `cargo run` without arguments to open the full-screen black-and-white terminal interface. On first launch, sign in with an OpenRouter account in your browser or enter an OpenRouter API key. Credentials are saved in the operating system credential store. Type a task and press Enter to request a plan. Use `/analyze`, `/files`, `/help`, `/clear`, and `/quit`; Esc or Ctrl+C exits. The planning service must be running for task requests.

```powershell
cargo run -- analyze .
cargo run -- list .
cargo run -- read README.md
cargo run -- search "TODO" .
cargo run -- login
cargo run -- task "Add a health endpoint" --workspace .
cargo run -- logout
```

Paths passed to `read` and `search` are resolved beneath the selected workspace. The scanner skips common generated and dependency directories.

`task` sends the saved provider API key and a bounded workspace summary to the local FastAPI service. The key is sent to the configured service URL, so keep `DEVPILOT_AGENT_URL` set to the local service or another HTTPS endpoint you trust. The summary contains a file listing and up to six relevant text files (48 KB total); `.env` files and files whose names suggest secrets or credentials are excluded. Set `DEVPILOT_PROVIDER` in both the CLI and planning service shells: `openrouter` (default), `openai`, `codex` (OpenAI API using a Codex model), `anthropic` (Claude), or `nvidia` (NVIDIA NIM). Set `DEVPILOT_MODEL` to the model ID and the matching key variable (`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `NVIDIA_API_KEY`) when you want service-side credentials. The CLI stores a separate key per provider; `devpilot logout` removes the key for the currently selected provider. OpenAI and NVIDIA use chat completions; Claude uses Anthropic's Messages API and receives the workspace summary. No local model download is needed. Model access and pricing depend on your provider account.

The API exposes `POST /v1/tools/read` and `POST /v1/tools/search` for bounded, validated workspace access. Secret-like files and symlinks are rejected. Task plans are saved in SQLite with `pending_approval`, `approved`, `rejected`, or `failed` status; use `GET /v1/tasks`, `GET /v1/tasks/{id}`, and `POST /v1/tasks/{id}/approve` or `/reject` to inspect and approve them. The database defaults to `%LOCALAPPDATA%\DevPilot\devpilot.sqlite3` on Windows and `$XDG_STATE_HOME/devpilot/devpilot.sqlite3` (or `~/.local/state/...`) elsewhere; set `DEVPILOT_DB_PATH` to override it.

Edits use `POST /v1/edits/preview` to persist a unified diff and source hash, followed by `POST /v1/edits/apply` with its `preview_id` and `approved: true`; stale previews are rejected. An edit linked to a task requires that task to be approved first. `POST /v1/tools/run` executes an argument-vector command in Docker with networking disabled, a 2 GB memory limit, two CPUs, a timeout, and up to three retries. Docker must be installed and its daemon running. Keep the API bound to localhost; do not expose it to an untrusted network.

The MCP Streamable HTTP endpoint is mounted at `/mcp` and exposes the same validated read/search, Docker command, diff-preview/apply, and browser tools. Browser tools use Playwright Chromium and allow public HTTP(S) destinations only; local/private hosts and subresources are blocked. Install the browser binary once with `python -m playwright install chromium` after installing Python requirements. Direct browser HTTP endpoints are also available at `POST /v1/tools/browser/open` and `/follow`.
The dashboard is served by the same API at `http://127.0.0.1:8000/`. It shows saved task runs, plan status, approval controls, tool activity, and the diff-preview/apply workflow.

## Run the planning service on Windows

Install Python 3.12 or newer and Docker Desktop. You can create an OpenRouter API key or sign in through the CLI's browser account flow. Then from PowerShell:

```powershell
cd C:\Users\swadh\Ai\agent
$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $Python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DEVPILOT_PROVIDER = "openrouter"
$env:DEVPILOT_MODEL = "particle-ai/particle-1:free"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, run the CLI from the repository root:

```powershell
cd C:\Users\swadh\Ai
$env:DEVPILOT_AGENT_URL = "http://127.0.0.1:8000"
cargo run -- task "Add a health endpoint" --workspace .
```

Run the API tests from `agent` with `.\.venv\Scripts\python.exe -m pytest -q`. On Windows, install the Visual Studio 2022 C++ Build Tools workload and run Rust tests from a Visual Studio Developer PowerShell with `cargo test`.

## Development

```powershell
cargo test
cargo build
```
