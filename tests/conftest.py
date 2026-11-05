"""Load the plugin as a package and isolate its database."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "hio"


def _ensure_package() -> None:
    if NAME in sys.modules and hasattr(sys.modules[NAME], "register"):
        return
    spec = importlib.util.spec_from_file_location(
        NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the plugin package")
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    module.__package__ = NAME
    sys.modules[NAME] = module
    spec.loader.exec_module(module)


_ensure_package()

import hio.service as service  # noqa: E402
import hio.store as store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_runtime():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    store.set_connection_factory(lambda: connection)
    service.set_client_factory(None)
    service.set_clock(None)
    yield connection
    store.set_connection_factory(None)
    service.set_client_factory(None)
    service.set_clock(None)
    connection.close()
