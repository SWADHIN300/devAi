"""HTTP API for local, read-only DevPilot task planning."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from .ollama_client import OllamaError, generate_plan

app = FastAPI(title="DevPilot Agent", version="0.1.0")


class PlanRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8_000)
    workspace_summary: str = Field(default="", max_length=65_000)

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
    """Ask Ollama for a task plan; this endpoint has no project-write tools."""
    try:
        plan, model = await generate_plan(payload.task, payload.workspace_summary)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OllamaError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return PlanResponse(plan=plan, model=model)
