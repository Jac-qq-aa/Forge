"""LangSmith execution context. Only internal services may restore these headers."""
from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator, Mapping

from langsmith.run_helpers import get_current_run_tree, tracing_context


_ALLOWED_HEADERS = frozenset({"langsmith-trace", "baggage"})


def current_headers() -> dict[str, str]:
    if os.getenv("LANGSMITH_TRACING", "false").lower() not in {"true", "1"}:
        return {}
    run = get_current_run_tree()
    if run is None:
        return {}
    return {key: value for key, value in run.to_headers().items()
            if key.lower() in _ALLOWED_HEADERS and isinstance(value, str)}


def current_trace_id() -> str | None:
    if os.getenv("LANGSMITH_TRACING", "false").lower() not in {"true", "1"}:
        return None
    run = get_current_run_tree()
    return str(run.trace_id) if run is not None else None


def trusted_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {key.lower(): value for key, value in headers.items()
            if key.lower() in _ALLOWED_HEADERS and isinstance(value, str) and len(value) <= 4096}


@contextmanager
def continue_trace(headers: Mapping[str, str] | None) -> Iterator[None]:
    parent = trusted_headers(headers)
    if parent and get_current_run_tree() is None:
        with tracing_context(parent=parent):
            yield
    else:
        yield
