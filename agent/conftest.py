"""Project-level pytest configuration."""

import os
from pathlib import Path

# Work around locked pytest temp directories on Windows by using a project-local
# temp path when the default system temp directory is inaccessible.
_PYTEST_TMP = Path(__file__).parent / ".pytest_tmp"


def pytest_configure(config) -> None:
    if os.name == "nt":
        username = os.environ.get("USERNAME", "user")
        default_tmp = Path(os.environ.get("TEMP", "")) / f"pytest-of-{username}"
        try:
            default_tmp.mkdir(parents=True, exist_ok=True)
            # Quick access check
            list(default_tmp.iterdir())
        except PermissionError:
            _PYTEST_TMP.mkdir(exist_ok=True)
            config.option.basetemp = str(_PYTEST_TMP)
