"""Docker-only project command runner with timeouts and bounded retries."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4


async def run_in_docker(workspace: str, command: list[str], image: str = "rust:1.94", timeout: int = 120, retries: int = 1) -> dict[str, object]:
    if not command or len(command) > 64 or any(len(arg) > 4_000 for arg in command):
        raise ValueError("command must contain between 1 and 64 arguments")
    if not 1 <= timeout <= 900:
        raise ValueError("timeout must be between 1 and 900 seconds")
    if not 0 <= retries <= 3:
        raise ValueError("retries must be between 0 and 3")
    if not image or len(image) > 256 or any(c.isspace() for c in image):
        raise ValueError("invalid Docker image")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace root must be a directory")

    attempts = []
    for attempt in range(retries + 1):
        container_name = f"devpilot-{uuid4().hex[:16]}"
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--name", container_name, "--network", "none", "--memory", "2g", "--cpus", "2",
            "--mount", f"type=bind,source={root},target=/workspace", "--workdir", "/workspace",
            image, *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            attempts.append({"exit_code": proc.returncode, "output": output.decode("utf-8", "replace")[-20_000:]})
            if proc.returncode == 0:
                break
        except TimeoutError:
            proc.kill()
            output, _ = await proc.communicate()
            cleanup = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await cleanup.wait()
            attempts.append({"exit_code": None, "timed_out": True, "output": output.decode("utf-8", "replace")[-20_000:]})
    return {"attempts": attempts, "success": bool(attempts and attempts[-1].get("exit_code") == 0)}
