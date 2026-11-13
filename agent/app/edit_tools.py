"""Diff-first, approval-gated workspace edits backed by durable previews."""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .store import StateStore

MAX_EDIT_BYTES = 1_000_000
BLOCKED_DIRS = {".git", ".venv", "venv", "target", "node_modules", "dist", "build"}
SECRET_PARTS = ("secret", "credential", "password", "api_key", "apikey", "access_key", "private_key", "id_rsa", "token")


def edit_target(root_value: str, relative: str) -> tuple[Path, Path]:
    root = Path(root_value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace root must be a directory")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("edit path must stay inside the workspace")
    if any(part in BLOCKED_DIRS for part in rel.parts):
        raise ValueError("editing generated, dependency, or version-control paths is not allowed")
    name = rel.name.lower()
    if name == ".env" or name.startswith(".env.") or any(part in name for part in SECRET_PARTS):
        raise ValueError("editing secret-like files is not allowed")
    candidate = root / rel
    if candidate.exists():
        target = candidate.resolve(strict=True)
    else:
        target = candidate.parent.resolve(strict=True) / candidate.name
    if not target.is_relative_to(root) or (target.exists() and not target.is_file()):
        raise ValueError("edit target must be a file inside the workspace")
    return root, target


def create_preview(
    store: StateStore,
    workspace_root: str,
    path: str,
    content: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_EDIT_BYTES:
        raise ValueError(f"new content exceeds the {MAX_EDIT_BYTES} byte limit")
    root, target = edit_target(workspace_root, path)
    old_bytes = target.read_bytes() if target.exists() else None
    if old_bytes is not None and len(old_bytes) > MAX_EDIT_BYTES:
        raise ValueError(f"edit target exceeds the {MAX_EDIT_BYTES} byte limit")
    old = old_bytes.decode("utf-8") if old_bytes is not None else ""
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
    )
    expected = hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None
    preview_id = store.create_edit_preview(str(root), path, content, diff, expected, task_id)
    return {"preview_id": preview_id, "path": path, "diff": diff, "expected_sha256": expected}


def apply_preview(store: StateStore, preview_id: str, approved: bool) -> dict[str, str]:
    if not approved:
        raise ValueError("explicit approval is required")
    preview = store.get_edit_preview(preview_id)
    if preview is None:
        raise KeyError("edit preview not found")
    if preview["applied_at"] is not None:
        raise ValueError("edit preview has already been applied or claimed")
    root, target = edit_target(preview["workspace_root"], preview["path"])
    current = target.read_bytes() if target.exists() else None
    current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
    if current_hash != preview["expected_sha256"]:
        raise ValueError("file changed since diff preview; create a new preview")
    fd, temp_name = tempfile.mkstemp(prefix=".devpilot-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp:
            temp.write(preview["content"])
        Path(temp_name).replace(target)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    if not target.is_relative_to(root) or not store.mark_edit_applied(preview_id):
        raise ValueError("edit could not be finalized safely")
    return {"preview_id": preview_id, "path": preview["path"], "status": "applied"}
