use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::process::ExitCode;

const SKIP_DIRS: &[&str] = &[
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "target",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
];
const MAX_FILE_BYTES: u64 = 1_000_000;
const MAX_SUMMARY_FILES: usize = 30;
const MAX_CONTEXT_FILES: usize = 6;
const MAX_CONTEXT_FILE_BYTES: usize = 12_000;
const MAX_CONTEXT_BYTES: usize = 48_000;

fn main() -> ExitCode {
    match run(env::args().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("error: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: Vec<String>) -> Result<(), String> {
    let Some(command) = args.first().map(String::as_str) else {
        return run_tui();
    };

    match command {
        "help" | "--help" | "-h" => print_help(),
        "--version" | "-V" => println!("devpilot {}", env!("CARGO_PKG_VERSION")),
        "analyze" => {
            let root = workspace_arg(args.get(1))?;
            analyze(&root)?;
        }
        "list" => {
            let root = workspace_arg(args.get(1))?;
            for path in collect_files(&root)? {
                println!("{}", path.display());
            }
        }
        "read" => {
            let relative = args.get(1).ok_or("usage: devpilot read <file>")?;
            let root = env::current_dir().map_err(|e| e.to_string())?;
            let path = resolve_file(&root, relative)?;
            let metadata = fs::metadata(&path).map_err(|e| format!("{}: {e}", path.display()))?;
            if metadata.len() > MAX_FILE_BYTES {
                return Err(format!(
                    "file exceeds the {} byte read limit",
                    MAX_FILE_BYTES
                ));
            }
            let mut contents = String::new();
            fs::File::open(&path)
                .and_then(|mut file| file.read_to_string(&mut contents))
                .map_err(|e| format!("cannot read {} as UTF-8 text: {e}", path.display()))?;
            print!("{contents}");
        }
        "search" => {
            let query = args
                .get(1)
                .ok_or("usage: devpilot search <text> [workspace]")?;
            if query.is_empty() {
                return Err("search text cannot be empty".into());
            }
            let root = workspace_arg(args.get(2))?;
            search(&root, query)?;
        }
        "task" => {
            let (request, workspace) = parse_task_args(&args[1..])?;
            let root = workspace_arg(workspace.as_ref())?;
            show_task_plan(&root, &request)?;
        }
        _ => return Err(format!("unknown command '{command}'. Run `devpilot help`.")),
    }
    Ok(())
}

fn run_tui() -> Result<(), String> {
    use crossterm::{
        event::{self, Event, KeyCode, KeyModifiers},
        terminal::{enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
        ExecutableCommand,
    };
    use ratatui::{
        backend::CrosstermBackend,
        layout::{Constraint, Direction, Layout},
        style::{Color, Modifier, Style},
        text::{Line, Span, Text},
        widgets::{Block, Borders, Clear, Paragraph, Wrap},
        Terminal,
    };

    struct TerminalSession;
    impl Drop for TerminalSession {
        fn drop(&mut self) {
            let _ = crossterm::terminal::disable_raw_mode();
            let _ = io::stdout().execute(LeaveAlternateScreen);
        }
    }

    enable_raw_mode().map_err(|e| format!("cannot enable terminal input: {e}"))?;
    let _session = TerminalSession;
    io::stdout()
        .execute(EnterAlternateScreen)
        .map_err(|e| format!("cannot open terminal screen: {e}"))?;
    let backend = CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend).map_err(|e| e.to_string())?;
    let mut input = String::new();
    let mut messages: Vec<(bool, String)> = Vec::new();
    let mut status = "Ready · plans require approval before any edits".to_string();
    let root = env::current_dir().map_err(|e| e.to_string())?;

    loop {
        terminal
            .draw(|frame| {
                let area = frame.area();
                let rows = Layout::default()
                    .direction(Direction::Vertical)
                    .constraints([Constraint::Length(3), Constraint::Min(5), Constraint::Length(4), Constraint::Length(1)])
                    .split(area);
                let white = Style::default().fg(Color::White).bg(Color::Black);
                let header = Paragraph::new(Line::from(vec![
                    Span::styled(" DEVPILOT ", white.add_modifier(Modifier::BOLD)),
                    Span::raw("  ·  "),
                    Span::styled(root.display().to_string(), white),
                ]))
                .block(Block::default().borders(Borders::BOTTOM).border_style(white));
                frame.render_widget(header, rows[0]);

                if messages.is_empty() {
                    let welcome = Paragraph::new(Text::from(vec![
                        Line::from(""),
                        Line::from(Span::styled("What are we building today?", white.add_modifier(Modifier::BOLD))),
                        Line::from("Describe a task and DevPilot will inspect this workspace and draft a plan."),
                        Line::from(""),
                        Line::from("  /help     keyboard shortcuts and commands"),
                        Line::from("  /analyze  summarize this workspace"),
                        Line::from("  /files    list project files"),
                    ]))
                    .style(white)
                    .alignment(ratatui::layout::Alignment::Center)
                    .block(Block::default().borders(Borders::ALL).border_style(white).title(" SESSION "));
                    frame.render_widget(welcome, rows[1]);
                } else {
                    let lines: Vec<Line> = messages
                        .iter()
                        .flat_map(|(user, message)| {
                            let role = if *user { " YOU " } else { " DEVPILOT " };
                            std::iter::once(Line::from(Span::styled(role, white.add_modifier(Modifier::BOLD))))
                                .chain(message.lines().map(|line| Line::from(Span::raw(line.to_string()))))
                                .chain(std::iter::once(Line::from("")))
                        })
                        .collect();
                    let conversation = Paragraph::new(lines)
                        .style(white)
                        .wrap(Wrap { trim: false })
                        .block(Block::default().borders(Borders::ALL).border_style(white).title(" SESSION "));
                    frame.render_widget(conversation, rows[1]);
                }

                let composer = Paragraph::new(Line::from(vec![
                    Span::styled(" > ", white.add_modifier(Modifier::BOLD)),
                    Span::raw(input.as_str()),
                ]))
                .style(white)
                .block(Block::default().borders(Borders::ALL).border_style(white).title(" MESSAGE · ENTER TO SEND "));
                frame.render_widget(Clear, rows[2]);
                frame.render_widget(composer, rows[2]);
                frame.render_widget(Paragraph::new(status.as_str()).style(white), rows[3]);
                let cursor_x = (rows[2].x + 4 + input.chars().count() as u16).min(rows[2].right().saturating_sub(2));
                frame.set_cursor_position((cursor_x, rows[2].y + 2));
            })
            .map_err(|e| e.to_string())?;

        if let Event::Key(key) = event::read().map_err(|e| e.to_string())? {
            if key.kind == event::KeyEventKind::Release {
                continue;
            }
            match key.code {
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => break,
                KeyCode::Esc => break,
                KeyCode::Backspace => { input.pop(); }
                KeyCode::Char(ch) if !key.modifiers.contains(KeyModifiers::CONTROL) => input.push(ch),
                KeyCode::Enter => {
                    let request = input.trim().to_string();
                    input.clear();
                    if request.is_empty() { continue; }
                    match request.as_str() {
                        "/quit" | "/exit" => break,
                        "/clear" => { messages.clear(); status = "Session cleared".into(); continue; }
                        "/help" => {
                            messages.push((false, "Type a task to generate a plan. Commands: /analyze, /files, /clear, /quit. Esc or Ctrl+C exits.".into()));
                            continue;
                        }
                        "/analyze" => {
                            match analyze_summary(&root) {
                                Ok(summary) => messages.push((false, summary)),
                                Err(error) => messages.push((false, format!("Error: {error}"))),
                            }
                            continue;
                        }
                        "/files" => {
                            match collect_files(&root) {
                                Ok(files) => {
                                    let shown = files.iter().take(100).map(|p| p.display().to_string()).collect::<Vec<_>>().join("\n");
                                    let suffix = if files.len() > 100 { format!("\n… and {} more", files.len() - 100) } else { String::new() };
                                    messages.push((false, format!("{} files\n{}{}", files.len(), shown, suffix)));
                                }
                                Err(error) => messages.push((false, format!("Error: {error}"))),
                            }
                            continue;
                        }
                        _ => {}
                    }
                    messages.push((true, request.clone()));
                    let files = collect_files(&root);
                    let result = files.and_then(|files| {
                        let summary = workspace_summary(&root, &files, &request);
                        let url = env::var("DEVPILOT_AGENT_URL").unwrap_or_else(|_| "http://127.0.0.1:8000".into());
                        request_plan(&url, &request, &summary, &root)
                    });
                    match result {
                        Ok((plan, model, run_id)) => {
                            messages.push((false, format!("{plan}\n\nModel: {model}\nRun: {run_id}\n\nReview the plan in the dashboard before approving edits.")));
                            status = format!("Plan ready · {run_id} · no files changed");
                        }
                        Err(error) => {
                            messages.push((false, format!("Could not prepare plan: {error}")));
                            status = "Request failed · check the planning service".into();
                        }
                    }
                }
                _ => {}
            }
        }
    }
    Ok(())
}

fn analyze_summary(root: &Path) -> Result<String, String> {
    let files = collect_files(root)?;
    let manifests = ["Cargo.toml", "package.json", "pyproject.toml", "requirements.txt", "go.mod", "README.md"];
    let found = files.iter().filter_map(|path| {
        let text = path.to_string_lossy();
        manifests.iter().find(|name| text.as_ref() == **name).map(|name| (*name).to_string())
    }).collect::<Vec<_>>();
    Ok(format!("Workspace: {}\nFiles: {}\nProject files: {}", root.display(), files.len(), if found.is_empty() { "none detected".into() } else { found.join(", ") }))
}

fn print_help() {
    println!(
        "DevPilot — local-first coding agent CLI foundation\n\n\
         Usage:\n\
         devpilot <command> [arguments]\n\n\
         Commands:\n\
         analyze [workspace]       Summarize project files and common manifests\n\
         list [workspace]          List project files\n\
         read <file>               Print a UTF-8 file under the current directory\n\
         search <text> [workspace] Find matching lines in project files\n\
         task <request> [--workspace <path>] Preview an agent plan for a task\n\
         help                      Show this help\n\n\
         Plans never apply file changes. Test commands may run in network-disabled Docker."
    );
}

fn parse_task_args(args: &[String]) -> Result<(String, Option<String>), String> {
    let mut request_words = Vec::new();
    let mut workspace = None;
    let mut index = 0;
    while index < args.len() {
        if args[index] == "--workspace" {
            if workspace.is_some() {
                return Err("--workspace may only be specified once".into());
            }
            let path = args.get(index + 1).ok_or("--workspace requires a path")?;
            if path.starts_with('-') {
                return Err("--workspace requires a path".into());
            }
            workspace = Some(path.clone());
            index += 2;
        } else if args[index].starts_with('-') {
            return Err(format!(
                "unknown task option '{}'; expected --workspace",
                args[index]
            ));
        } else {
            request_words.push(args[index].as_str());
            index += 1;
        }
    }
    let request = request_words.join(" ").trim().to_string();
    if request.is_empty() {
        return Err("usage: devpilot task <request> [--workspace <path>]".into());
    }
    Ok((request, workspace))
}

fn show_task_plan(root: &Path, request: &str) -> Result<(), String> {
    let files = collect_files(root)?;
    let summary = workspace_summary(root, &files, request);
    let service_url =
        env::var("DEVPILOT_AGENT_URL").unwrap_or_else(|_| "http://127.0.0.1:8000".into());
    let (plan, model, run_id) = request_plan(&service_url, request, &summary, root)?;
    println!("Task: {request}");
    println!("Workspace: {}", root.display());
    println!("Planning model: {model}");
    println!("Plan run: {run_id} (approval required before linked edits)");
    println!("\nModel plan (files are not changed; tests may run in Docker):\n{plan}");
    Ok(())
}

fn workspace_summary(root: &Path, files: &[PathBuf], request: &str) -> String {
    let manifests = [
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "README.md",
    ];
    let detected: Vec<String> = files
        .iter()
        .filter_map(|p| {
            let s = p.to_string_lossy();
            manifests
                .iter()
                .find(|m| s.as_ref() == **m)
                .map(|m| (*m).to_string())
        })
        .collect();
    let listing = files
        .iter()
        .take(MAX_SUMMARY_FILES)
        .map(|p| p.display().to_string())
        .collect::<Vec<_>>()
        .join("\n");
    let context = relevant_file_context(root, files, request);
    format!(
        "Workspace root: {}\nFile count: {}\nCommon project files: {}\nFiles (up to {MAX_SUMMARY_FILES}):\n{}",
        root.display(),
        files.len(),
        if detected.is_empty() {
            "none detected".into()
        } else {
            detected.join(", ")
        },
        listing
    ) + &format!("\n\nRelevant file contents (bounded context):\n{context}")
}

fn relevant_file_context(root: &Path, files: &[PathBuf], request: &str) -> String {
    let mut candidates: Vec<&PathBuf> = files
        .iter()
        .filter(|path| is_safe_context_file(path))
        .collect();
    candidates.sort_by_key(|path| {
        let text = path.to_string_lossy().to_lowercase();
        let name = path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_lowercase();
        let is_manifest = matches!(
            name.as_str(),
            "cargo.toml" | "package.json" | "pyproject.toml" | "go.mod" | "readme.md"
        );
        let request_match = request
            .split(|c: char| !c.is_alphanumeric())
            .filter(|word| word.len() > 2)
            .any(|word| text.contains(&word.to_lowercase()));
        (!request_match, !is_manifest, text)
    });

    let mut included = 0;
    let mut total_bytes = 0;
    let mut context = String::new();
    for relative in candidates {
        if included >= MAX_CONTEXT_FILES || total_bytes >= MAX_CONTEXT_BYTES {
            break;
        }
        let Ok(metadata) = fs::metadata(root.join(relative)) else {
            continue;
        };
        if metadata.len() > MAX_CONTEXT_FILE_BYTES as u64 {
            continue;
        }
        let Ok(contents) = fs::read_to_string(root.join(relative)) else {
            continue;
        };
        let remaining = MAX_CONTEXT_BYTES - total_bytes;
        let contents = truncate_utf8(&contents, MAX_CONTEXT_FILE_BYTES.min(remaining));
        if contents.is_empty() {
            continue;
        }
        context.push_str(&format!("\n--- {} ---\n{}\n", relative.display(), contents));
        total_bytes += contents.len();
        included += 1;
    }
    if context.is_empty() {
        "No eligible text files found.".into()
    } else {
        context
    }
}

fn is_safe_context_file(path: &Path) -> bool {
    let name = path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    if name.starts_with('.') && (name == ".env" || name.starts_with(".env.")) {
        return false;
    }
    if [
        "secret",
        "credential",
        "password",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "id_rsa",
        "token",
    ]
    .iter()
    .any(|part| name.contains(part))
    {
        return false;
    }
    matches!(
        path.extension()
            .and_then(|ext| ext.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some(
            "rs" | "toml"
                | "json"
                | "md"
                | "txt"
                | "py"
                | "js"
                | "jsx"
                | "ts"
                | "tsx"
                | "go"
                | "java"
                | "kt"
                | "c"
                | "h"
                | "cpp"
                | "cs"
                | "html"
                | "css"
                | "yaml"
                | "yml"
        )
    ) || matches!(name.as_str(), "makefile" | "dockerfile")
}

fn truncate_utf8(text: &str, max_bytes: usize) -> &str {
    if text.len() <= max_bytes {
        return text;
    }
    let mut end = max_bytes;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

fn request_plan(
    base_url: &str,
    request: &str,
    summary: &str,
    root: &Path,
) -> Result<(String, String, String), String> {
    let base = base_url.trim_end_matches('/');
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()
        .map_err(|e| format!("cannot create planning-service client: {e}"))?;
    let response = client
        .post(format!("{base}/v1/plan"))
        .json(&serde_json::json!({
            "task": request,
            "workspace_summary": summary,
            "workspace_root": root
        }))
        .send()
        .map_err(|e| {
            format!(
                "DevPilot planning service is unavailable at {base} ({e}). Start it with `python -m uvicorn app.main:app --app-dir agent --host 127.0.0.1 --port 8000` or set DEVPILOT_AGENT_URL."
            )
        })?;
    if !response.status().is_success() {
        return Err(format!(
            "DevPilot planning service returned HTTP {}. Check its output for provider details.",
            response.status(),
        ));
    }
    let result: serde_json::Value = response
        .json()
        .map_err(|e| format!("invalid response from DevPilot planning service: {e}"))?;
    let plan = result["plan"]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| "DevPilot planning service returned an empty plan".to_string())?;
    let model = result["model"]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("unknown")
        .to_string();
    let run_id = result["run_id"]
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| "DevPilot planning service returned no task run ID".to_string())?
        .to_string();
    Ok((plan, model, run_id))
}

fn workspace_arg(arg: Option<&String>) -> Result<PathBuf, String> {
    let root = arg.map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
    let canonical = root
        .canonicalize()
        .map_err(|e| format!("cannot open workspace {}: {e}", root.display()))?;
    if !canonical.is_dir() {
        return Err(format!(
            "workspace is not a directory: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

fn collect_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    walk(root, root, &mut files)?;
    files.sort();
    Ok(files)
}

fn walk(root: &Path, dir: &Path, files: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = fs::read_dir(dir).map_err(|e| format!("cannot list {}: {e}", dir.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let file_type = entry.file_type().map_err(|e| e.to_string())?;
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            let name = entry.file_name();
            if name.to_str().is_some_and(|name| SKIP_DIRS.contains(&name)) {
                continue;
            }
            walk(root, &path, files)?;
        } else if file_type.is_file() {
            if let Ok(relative) = path.strip_prefix(root) {
                files.push(relative.to_path_buf());
            }
        }
    }
    Ok(())
}

fn resolve_file(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(relative);
    if candidate.is_absolute()
        || candidate
            .components()
            .any(|c| matches!(c, Component::ParentDir))
    {
        return Err("file path must stay inside the workspace".into());
    }
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let path = root
        .join(candidate)
        .canonicalize()
        .map_err(|e| format!("cannot resolve {}: {e}", candidate.display()))?;
    if !path.starts_with(&root) {
        return Err("file path must stay inside the workspace".into());
    }
    if !path.is_file() {
        return Err(format!("not a file: {}", path.display()));
    }
    Ok(path)
}

fn analyze(root: &Path) -> Result<(), String> {
    let files = collect_files(root)?;
    println!("Workspace: {}", root.display());
    println!("Files: {}", files.len());
    let manifests = [
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Makefile",
        "README.md",
    ];
    let found: Vec<_> = files
        .iter()
        .filter_map(|p| {
            let text = p.to_string_lossy();
            manifests
                .iter()
                .find(|name| text == **name)
                .map(|name| (*name, p))
        })
        .collect();
    if found.is_empty() {
        println!("Common manifests: none detected");
    } else {
        println!("Common project files:");
        for (name, path) in found {
            println!("  {name}: {}", path.display());
        }
    }
    Ok(())
}

fn search(root: &Path, query: &str) -> Result<(), String> {
    let files = collect_files(root)?;
    let mut matches = 0usize;
    for relative in files {
        let full_path = root.join(&relative);
        let Ok(metadata) = fs::metadata(&full_path) else {
            continue;
        };
        if metadata.len() > MAX_FILE_BYTES {
            continue;
        }
        let Ok(contents) = fs::read_to_string(&full_path) else {
            continue;
        };
        for (index, line) in contents.lines().enumerate() {
            if line.contains(query) {
                println!("{}:{}:{}", relative.display(), index + 1, line.trim());
                matches += 1;
            }
        }
    }
    if matches == 0 {
        println!("No matches found.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_parent_directory_paths() {
        let root = env::current_dir().unwrap();
        assert!(resolve_file(&root, "../Cargo.toml").is_err());
    }

    #[test]
    fn accepts_workspace_file() {
        let root = env::current_dir().unwrap();
        assert!(resolve_file(&root, "Cargo.toml").is_ok());
    }

    #[test]
    fn parses_multi_word_task_and_workspace_option() {
        let args = vec![
            "add".into(),
            "a".into(),
            "health endpoint".into(),
            "--workspace".into(),
            ".".into(),
        ];
        let (request, workspace) = parse_task_args(&args).unwrap();
        assert_eq!(request, "add a health endpoint");
        assert_eq!(workspace.as_deref(), Some("."));
    }

    #[test]
    fn rejects_task_without_request() {
        assert!(parse_task_args(&[]).is_err());
    }

    #[test]
    fn sends_task_to_planning_service_and_reads_plan() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::thread;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0; 4096];
            let mut expected_length = None;
            loop {
                let count = stream.read(&mut buffer).unwrap();
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..count]);
                if expected_length.is_none() {
                    if let Some(header_end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&request[..header_end]);
                        expected_length = headers.lines().find_map(|line| {
                            let (name, value) = line.split_once(':')?;
                            name.eq_ignore_ascii_case("content-length")
                                .then(|| value.trim().parse::<usize>().ok())
                                .flatten()
                        });
                    }
                }
                if let (Some(header_end), Some(body_length)) = (
                    request.windows(4).position(|w| w == b"\r\n\r\n"),
                    expected_length,
                ) {
                    if request.len() >= header_end + 4 + body_length {
                        break;
                    }
                }
            }
            let body = r#"{"run_id":"run-123","plan":"1. Inspect the route.\n2. Add the endpoint.","model":"test-model"}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
            String::from_utf8(request).unwrap()
        });

        let (plan, model, run_id) = request_plan(
            &format!("http://{address}"),
            "Add a health endpoint",
            "Files: src/main.rs",
            Path::new("."),
        )
        .unwrap();
        let request = server.join().unwrap();
        let body = request.split_once("\r\n\r\n").unwrap().1;
        let payload: serde_json::Value = serde_json::from_str(body).unwrap();

        assert!(request.starts_with("POST /v1/plan HTTP/1.1"));
        assert_eq!(payload["task"], "Add a health endpoint");
        assert_eq!(payload["workspace_summary"], "Files: src/main.rs");
        assert!(payload["workspace_root"].is_string());
        assert_eq!(plan, "1. Inspect the route.\n2. Add the endpoint.");
        assert_eq!(model, "test-model");
        assert_eq!(run_id, "run-123");
    }
}
