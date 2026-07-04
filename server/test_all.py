"""
Pytest entry point for the whole server suite.

The individual test_*.py files are standalone scripts: each defines a main() that asserts and
prints, run directly as `python test_foo.py`. That's still supported, but it means `pytest`
collected nothing (no test_* functions), so there was no one-command "run everything" for CI.

This module parametrizes over every sibling test_*.py and runs its main(), so `pytest server`
(or `pytest -k double_login`, `-x`, etc.) drives the exact same checks. Each script re-points
persistence at a throwaway store in its own main(), so running them in one process is safe.
"""
import importlib
import pathlib

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_MODULES = sorted(
    p.stem for p in _HERE.glob("test_*.py")
    if p.stem != "test_all" and hasattr(importlib.import_module(p.stem), "main")
)


@pytest.mark.parametrize("modname", _MODULES)
def test_script(modname):
    """Run one standalone test script's main() as a pytest case."""
    importlib.import_module(modname).main()
