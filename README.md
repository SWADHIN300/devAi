# DevPilot CLI

A local-first Rust CLI and Python planning service for exploring software projects. The `task` command asks a local Ollama model for a read-only plan; the CLI does not modify files or execute project commands.

## Commands

```powershell
cargo run -- analyze .
cargo run -- list .
cargo run -- read README.md
cargo run -- search "TODO" .
cargo run -- task "Add a health endpoint" --workspace .
```

Paths passed to `read` and `search` are resolved beneath the selected workspace. The scanner skips common generated and dependency directories.

`task` sends the request and a bounded workspace summary to the local FastAPI service. The summary contains a file listing and up to six relevant text files (48 KB total); `.env` files and files whose names suggest secrets or credentials are excluded. FastAPI calls Ollama and returns a plan. Set `DEVPILOT_AGENT_URL` (default `http://127.0.0.1:8000`), `DEVPILOT_OLLAMA_URL` (default `http://127.0.0.1:11434`), and `DEVPILOT_OLLAMA_MODEL` (default `qwen2.5-coder:7b`) to configure the local services. The model must already be installed (`ollama pull <model>`). The command only prints a plan; it does not edit files or run commands.

## Run the planning service on Windows

Install Python 3.12 or newer, then from PowerShell:

```powershell
cd C:\Users\swadh\Ai\agent
$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $Python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DEVPILOT_OLLAMA_URL = "http://127.0.0.1:11434"
$env:DEVPILOT_OLLAMA_MODEL = "qwen2.5-coder:7b"
ollama pull $env:DEVPILOT_OLLAMA_MODEL
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, run the CLI from the repository root:

```powershell
cd C:\Users\swadh\Ai
$env:DEVPILOT_AGENT_URL = "http://127.0.0.1:8000"
cargo run -- task "Add a health endpoint" --workspace .
```

Run the API tests from `agent` with `.\.venv\Scripts\python.exe -m pytest -q` and the Rust tests from the repository root with `cargo test`.

## Development

```powershell
cargo test
cargo build
```
