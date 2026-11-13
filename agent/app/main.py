"""HTTP API for DevPilot planning, persistence, and gated workspace tools."""

from contextlib import asynccontextmanager
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .browser_tools import BrowserToolError, browse_url
from .edit_tools import apply_preview, create_preview
from .mcp_server import mcp
from .openrouter_client import OpenRouterError, generate_plan
from .command_runner import run_in_docker
from .store import StateStore
from .workspace_tools import read_file, search_code

mcp_http_app = mcp.streamable_http_app(
    json_response=True,
    stateless_http=True,
    streamable_http_path="/",
)


@asynccontextmanager
async def lifespan(_app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="DevPilot Agent", version="0.2.0", lifespan=lifespan)
app.mount("/mcp", mcp_http_app, name="mcp")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
    run_id: str
    plan: str
    model: str


@app.get("/health")
async def health() -> dict[str, str]:
    """Report that the local planning API is accepting requests."""
    return {"status": "ok"}


@app.post("/v1/plan", response_model=PlanResponse)
async def create_plan(payload: PlanRequest) -> PlanResponse:
    """Create and persist a task plan that awaits explicit user approval."""
    store = StateStore()
    task_id = store.create_run(payload.task, payload.workspace_root)
    try:
        plan, model = await generate_plan(
            payload.task, payload.workspace_summary, payload.workspace_root, task_id
        )
    except ValueError as exc:
        store.fail_run(task_id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OpenRouterError as exc:
        store.fail_run(task_id, str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception:
        store.fail_run(task_id, "planning failed")
        raise
    store.complete_run(task_id, plan, model)
    return PlanResponse(run_id=task_id, plan=plan, model=model)


@app.get("/v1/tasks")
async def list_tasks(limit: int = 50) -> dict[str, object]:
    try:
        return {"tasks": StateStore().list_runs(limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, object]:
    store = StateStore()
    task = store.get_run(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    task["tool_events"] = store.list_tool_events(task_id)
    return task


@app.post("/v1/tasks/{task_id}/approve")
async def approve_task(task_id: str) -> dict[str, str]:
    if not StateStore().approve_run(task_id):
        raise HTTPException(status_code=409, detail="task is missing or is not awaiting approval")
    return {"id": task_id, "status": "approved"}


@app.post("/v1/tasks/{task_id}/reject")
async def reject_task(task_id: str) -> dict[str, str]:
    if not StateStore().reject_run(task_id):
        raise HTTPException(status_code=409, detail="task is missing or is not awaiting approval")
    return {"id": task_id, "status": "rejected"}


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
    task_id: str | None = Field(default=None, max_length=64)


class ApplyEditRequest(BaseModel):
    preview_id: str = Field(min_length=1, max_length=64)
    approved: bool = False


@app.post("/v1/tools/read")
async def tool_read(payload: ReadRequest) -> dict[str, str]:
    """Validated file-reading tool for an agent operating on one workspace."""
    started = time.monotonic()
    succeeded = False
    try:
        result = {"path": payload.path, "content": read_file(payload.workspace_root, payload.path)}
        succeeded = True
        return result
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        StateStore().record_tool_event("read_file", succeeded, int((time.monotonic() - started) * 1000))


@app.post("/v1/tools/search")
async def tool_search(payload: SearchRequest) -> dict[str, object]:
    """Validated code-search tool for an agent operating on one workspace."""
    started = time.monotonic()
    succeeded = False
    try:
        result = {"matches": search_code(payload.workspace_root, payload.query, payload.limit)}
        succeeded = True
        return result
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        StateStore().record_tool_event("search_code", succeeded, int((time.monotonic() - started) * 1000))


@app.post("/v1/tools/run")
async def tool_run(payload: RunRequest) -> dict[str, object]:
    """Run a command in a network-disabled, resource-bounded Docker container."""
    started = time.monotonic()
    succeeded = False
    try:
        result = await run_in_docker(
            payload.workspace_root, payload.command, payload.image, payload.timeout, payload.retries
        )
        succeeded = bool(result.get("success"))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail=f"Docker command runner unavailable: {exc}") from exc
    finally:
        StateStore().record_tool_event("run_tests", succeeded, int((time.monotonic() - started) * 1000))


class BrowserRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4_000)


class BrowserFollowRequest(BrowserRequest):
    link_text: str = Field(min_length=1, max_length=500)


@app.post("/v1/tools/browser/open")
async def browser_open(payload: BrowserRequest) -> dict[str, object]:
    started = time.monotonic()
    succeeded = False
    try:
        result = await browse_url(payload.url)
        succeeded = True
        return result
    except BrowserToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        StateStore().record_tool_event("browser_open", succeeded, int((time.monotonic() - started) * 1000))


@app.post("/v1/tools/browser/follow")
async def browser_follow(payload: BrowserFollowRequest) -> dict[str, object]:
    started = time.monotonic()
    succeeded = False
    try:
        result = await browse_url(payload.url, follow_link_text=payload.link_text)
        succeeded = True
        return result
    except BrowserToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        StateStore().record_tool_event("browser_follow_link", succeeded, int((time.monotonic() - started) * 1000))


@app.post("/v1/edits/preview")
async def preview_edit(payload: EditRequest) -> dict[str, object]:
    """Persist a unified diff; a tied task must already have explicit plan approval."""
    try:
        return create_preview(
            StateStore(), payload.workspace_root, payload.path, payload.content, payload.task_id
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/edits/apply")
async def apply_edit(payload: ApplyEditRequest) -> dict[str, str]:
    """Apply exactly the persisted preview after explicit approval and a fresh hash check."""
    if not payload.approved:
        raise HTTPException(status_code=409, detail="explicit approval is required")
    try:
        return apply_preview(StateStore(), payload.preview_id, payload.approved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, UnicodeError, ValueError) as exc:
        status = 409 if "changed since" in str(exc) or "already been applied" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
