"""Small, validated workspace tools exposed to DevPilot's planner."""

from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "target", "node_modules", "dist", "build", "__pycache__"}
MAX_FILE_BYTES = 1_000_000
SECRET_PARTS = ("secret", "credential", "password", "api_key", "apikey", "access_key", "private_key", "id_rsa", "token")


def _root(path: str) -> Path:
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace root must be a directory")
    return root


def _file(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("file path must stay inside the workspace")
    path = (root / candidate).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("file path must resolve to a workspace file")
    name = path.name.lower()
    if name == ".env" or name.startswith(".env.") or any(part in name for part in SECRET_PARTS):
        raise ValueError("reading secret-like files is not allowed")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds the {MAX_FILE_BYTES} byte limit")
    return path


def read_file(workspace: str, path: str) -> str:
    """Read a bounded UTF-8 file beneath the supplied workspace."""
    return _file(_root(workspace), path).read_text(encoding="utf-8")


def search_code(workspace: str, query: str, limit: int = 100) -> list[dict[str, str | int]]:
    """Search bounded UTF-8 files while skipping generated and secret files."""
    if not query.strip():
        raise ValueError("search query cannot be empty")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    root = _root(workspace)
    matches: list[dict[str, str | int]] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            safe_path = _file(root, path.relative_to(root).as_posix())
            lines = safe_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError, ValueError):
            continue
        for number, line in enumerate(lines, 1):
            if query in line:
                matches.append({"path": path.relative_to(root).as_posix(), "line": number, "text": line[:2000]})
                if len(matches) >= limit:
                    return matches
    return matches
