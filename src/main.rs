use std::env;
use std::fs;
use std::io::Read;
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
        print_help();
        return Ok(());
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
         task <request> [--workspace <path>] Preview a read-only plan for a task\n\
         help                      Show this help\n\n\
         No files are modified and no project commands are executed."
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
    println!("Task: {request}");
    println!("Workspace: {}", root.display());
    println!("Planning mode: local preview (no LLM, no file changes, no commands)");
    println!("\nWorkspace context: {} files", files.len());
    for file in files.iter().take(10) {
        println!("  {}", file.display());
    }
    if files.len() > 10 {
        println!("  ... and {} more", files.len() - 10);
    }
    println!("\nProposed workflow:");
    println!("  1. Inspect files related to the request: {request}");
    println!("  2. Identify the smallest set of files and changes needed.");
    println!("  3. Present the intended edits for review before writing files.");
    println!("  4. After approval and implementation, run relevant checks and report results.");
    println!("\nThis is a generic preview; connect Ollama for task-specific planning.");
    Ok(())
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
}
