"""MCP tools exposing DevPilot's validated workspace and browser operations."""

from __future__ import annotations

import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from .browser_tools import browse_url
from .command_runner import run_in_docker
from .edit_tools import apply_preview, create_preview
from .store import StateStore
from .workspace_tools import read_file as workspace_read_file
from .workspace_tools import search_code as workspace_search_code

mcp = MCPServer(
    "DevPilot",
    instructions=(
        "Read and search only within a caller-provided workspace. File edits require a diff preview, "
        "an approved plan when attached, and explicit approval when applying. Commands run in a "
        "network-disabled Docker container. Browser tools can access public HTTP(S) sites only."
    ),
)


def _record(name: str, started: float, succeeded: bool, task_id: str | None = None) -> None:
    StateStore().record_tool_event(name, succeeded, int((time.monotonic() - started) * 1000), task_id)


@mcp.tool()
def read_file(workspace_root: str, path: str) -> dict[str, str]:
    """Read a bounded UTF-8 file inside a workspace, excluding secret-like files."""
    started = time.monotonic()
    succeeded = False
    try:
        result = {"path": path, "content": workspace_read_file(workspace_root, path)}
        succeeded = True
        return result
    finally:
        _record("read_file", started, succeeded)


@mcp.tool()
def search_code(workspace_root: str, query: str, limit: int = 100) -> dict[str, Any]:
    """Find a literal string in bounded workspace text files."""
    started = time.monotonic()
    succeeded = False
    try:
        result = {"matches": workspace_search_code(workspace_root, query, limit)}
        succeeded = True
        return result
    finally:
        _record("search_code", started, succeeded)


@mcp.tool()
async def run_tests(workspace_root: str, command: list[str], timeout: int = 120, retries: int = 1) -> dict[str, Any]:
    """Run a command in a network-disabled Docker container with bounded resources and retries."""
    started = time.monotonic()
    succeeded = False
    try:
        result = await run_in_docker(workspace_root, command, timeout=timeout, retries=retries)
        succeeded = bool(result.get("success"))
        return result
    finally:
        _record("run_tests", started, succeeded)


@mcp.tool()
def preview_file_edit(
    workspace_root: str,
    path: str,
    content: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create and persist a unified diff; an attached task plan must already be approved."""
    started = time.monotonic()
    succeeded = False
    try:
        result = create_preview(StateStore(), workspace_root, path, content, task_id)
        succeeded = True
        return result
    finally:
        _record("preview_file_edit", started, succeeded, task_id)


@mcp.tool()
def apply_file_edit(preview_id: str, approved: bool) -> dict[str, str]:
    """Apply the exact persisted diff only after explicit user approval and a fresh hash check."""
    started = time.monotonic()
    succeeded = False
    try:
        result = apply_preview(StateStore(), preview_id, approved)
        succeeded = True
        return result
    finally:
        _record("apply_file_edit", started, succeeded)


@mcp.tool()
async def browser_open(url: str) -> dict[str, Any]:
    """Open a public HTTP(S) page in Chromium and return bounded page text and links."""
    started = time.monotonic()
    succeeded = False
    try:
        result = await browse_url(url)
        succeeded = True
        return result
    finally:
        _record("browser_open", started, succeeded)


@mcp.tool()
async def browser_follow_link(url: str, link_text: str) -> dict[str, Any]:
    """Open a public HTTP(S) page, follow one exact link label, and return bounded page text."""
    started = time.monotonic()
    succeeded = False
    try:
        result = await browse_url(url, follow_link_text=link_text)
        succeeded = True
        return result
    finally:
        _record("browser_follow_link", started, succeeded)
