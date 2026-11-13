# DevPilot CLI

A small, local-first Rust CLI foundation for exploring a software project. This first milestone provides read-only project inspection; it does not call an LLM or execute project commands.

## Commands

```powershell
cargo run -- analyze .
cargo run -- list .
cargo run -- read README.md
cargo run -- search "TODO" .
cargo run -- task "Add a health endpoint" --workspace .
```

Paths passed to `read` and `search` are resolved beneath the selected workspace. The scanner skips common generated and dependency directories.

`task` prints the request, a small workspace snapshot, and a generic proposed workflow. It is currently a read-only preview: it does not call Ollama, edit files, or run commands.

## Development

```powershell
cargo test
cargo build
```
