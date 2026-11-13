"""HTTP API for DevPilot planning and gated workspace tools."""

import difflib
import hashlib
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from .openrouter_client import OpenRouterError, generate_plan
from .workspace_tools import read_file, search_code
from .command_runner import run_in_docker

app = FastAPI(title="DevPilot Agent", version="0.1.0")


class PlanRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8_000)
    workspace_summary: str = Field(default="", max_length=65_000)
    workspace_root: str = Field(default=".", max_length=4_000)

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task cannot be blank")
        return value.strip()


class PlanResponse(BaseModel):
    plan: str
    model: str


@app.get("/health")
async def health() -> dict[str, str]:
    """Report that the local planning API is accepting requests."""
    return {"status": "ok"}


@app.post("/v1/plan", response_model=PlanResponse)
async def create_plan(payload: PlanRequest) -> PlanResponse:
    """Ask the configured model for a task plan; this endpoint does not write project files."""
    try:
        plan, model = await generate_plan(payload.task, payload.workspace_summary, payload.workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return PlanResponse(plan=plan, model=model)


class ReadRequest(BaseModel):
    workspace_root: str = Field(min_length=1, max_length=4_000)
    path: str = Field(min_length=1, max_length=4_000)


class SearchRequest(BaseModel):
    workspace_root: str = Field(min_length=1, max_length=4_000)
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=100, ge=1, le=500)


class RunRequest(BaseModel):
    workspace_root: str = Field(min_length=1, max_length=4_000)
    command: list[str] = Field(min_length=1, max_length=64)
    image: str = Field(default="rust:1.94", max_length=256)
    timeout: int = Field(default=120, ge=1, le=900)
    retries: int = Field(default=1, ge=0, le=3)


class EditRequest(BaseModel):
    workspace_root: str = Field(min_length=1, max_length=4_000)
    path: str = Field(min_length=1, max_length=4_000)
    content: str = Field(max_length=1_000_000)


class ApplyEditRequest(EditRequest):
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approved: bool = False


def _edit_target(root_value: str, relative: str, allow_new: bool) -> tuple[Path, Path]:
    root = Path(root_value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace root must be a directory")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("edit path must stay inside the workspace")
    if any(part in {".git", ".venv", "venv", "target", "node_modules", "dist", "build"} for part in rel.parts):
        raise ValueError("editing generated, dependency, or version-control paths is not allowed")
    name = rel.name.lower()
    if name == ".env" or name.startswith(".env.") or any(
        part in name for part in ("secret", "credential", "password", "api_key", "apikey", "access_key", "private_key", "id_rsa", "token")
    ):
        raise ValueError("editing secret-like files is not allowed")
    target = root / rel
    if target.exists():
        target = target.resolve(strict=True)
    elif allow_new:
        target = target.parent.resolve(strict=True) / target.name
    else:
        raise ValueError("edit target does not exist")
    if not target.is_relative_to(root) or (target.exists() and not target.is_file()):
        raise ValueError("edit target must be a file inside the workspace")
    return root, target


@app.post("/v1/tools/read")
async def tool_read(payload: ReadRequest) -> dict[str, str]:
    """Validated file-reading tool for an agent operating on one workspace."""
    try:
        return {"path": payload.path, "content": read_file(payload.workspace_root, payload.path)}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/tools/search")
async def tool_search(payload: SearchRequest) -> dict[str, object]:
    """Validated code-search tool for an agent operating on one workspace."""
    try:
        return {"matches": search_code(payload.workspace_root, payload.query, payload.limit)}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/tools/run")
async def tool_run(payload: RunRequest) -> dict[str, object]:
    """Run a command in a network-disabled, resource-bounded Docker container."""
    try:
        return await run_in_docker(
            payload.workspace_root, payload.command, payload.image, payload.timeout, payload.retries
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail=f"Docker command runner unavailable: {exc}") from exc


@app.post("/v1/edits/preview")
async def preview_edit(payload: EditRequest) -> dict[str, object]:
    """Show a unified diff and a content hash before an edit can be applied."""
    try:
        _, target = _edit_target(payload.workspace_root, payload.path, allow_new=True)
        if target.exists() and target.stat().st_size > 1_000_000:
            raise ValueError("edit target exceeds the 1,000,000 byte limit")
        old_bytes = target.read_bytes() if target.exists() else None
        old = old_bytes.decode("utf-8") if old_bytes is not None else ""
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), payload.content.splitlines(keepends=True),
        fromfile=f"a/{payload.path}", tofile=f"b/{payload.path}",
    ))
    return {"path": payload.path, "diff": diff, "expected_sha256": hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None}


@app.post("/v1/edits/apply")
async def apply_edit(payload: ApplyEditRequest) -> dict[str, str]:
    """Apply a previously reviewed edit only with explicit approval and no race."""
    if not payload.approved:
        raise HTTPException(status_code=409, detail="explicit approval is required")
    try:
        root, target = _edit_target(payload.workspace_root, payload.path, allow_new=True)
        if target.exists() and target.stat().st_size > 1_000_000:
            raise ValueError("edit target exceeds the 1,000,000 byte limit")
        current = target.read_bytes() if target.exists() else None
        current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
        if current_hash != payload.expected_sha256:
            raise HTTPException(status_code=409, detail="file changed since diff preview; preview again")
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="edit target escaped the workspace")
        fd, temp_name = tempfile.mkstemp(prefix=".devpilot-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as temp:
                temp.write(payload.content)
            Path(temp_name).replace(target)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return {"path": payload.path, "status": "applied"}
    except HTTPException:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
