"""Host-configured acceptance guard. This is not an OS permission sandbox."""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
from typing import Any

from .acceptance_control import AcceptanceController


_active_operation: contextvars.ContextVar[tuple[int, str, str, str] | None] = contextvars.ContextVar(
    "finite_acceptance_service_operation", default=None
)
_INTERNAL_CALLS = {
    "audit_storyboard": {"begin_task"},
    "commit_storyboard": {"commit_storyboard"},
    "commit_storyboard_delta": {"commit_storyboard"},
    "commit_storyboard_patch": {"commit_storyboard"},
}


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def operation_request_sha256(method: Any, *args: Any, **kwargs: Any) -> str:
    """Hash bound business arguments including defaults; no self or transport session."""
    bound = inspect.signature(method).bind(*args, **kwargs)
    bound.apply_defaults()
    payload = {key: _json_value(value) for key, value in bound.arguments.items()
               if key not in {"self", "request_session_id"}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def controlled_acceptance_operation(method: Any) -> Any:
    """Consume once at the outer service boundary, before authority/storage writes.

    Nested service calls are execution of the already bound outer request. They
    cannot change project or service instance. The caller has no lease argument.
    """
    signature = inspect.signature(method)

    @functools.wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        settings = self.settings
        if not settings.acceptance_control_enabled:
            return method(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        if method.__name__ == "agent_status" and not bound.arguments["full_self_test"]:
            return method(self, *args, **kwargs)
        namespace = str(bound.arguments.get("namespace") or "")
        project_id = str(bound.arguments.get("project_id") or "")
        identity = (id(self), namespace, project_id)
        active = _active_operation.get()
        if active is not None:
            if active[:3] != identity:
                raise ValueError("acceptance_nested_scope_conflict")
            if method.__name__ not in _INTERNAL_CALLS.get(active[3], set()):
                raise ValueError("acceptance_nested_operation_not_allowed")
            return method(self, *args, **kwargs)
        if not settings.acceptance_registry_dir or not settings.acceptance_lease_id:
            raise ValueError("acceptance_lease_required: host must reserve a frozen step first")
        controller = AcceptanceController(settings.acceptance_registry_dir)
        controller.consume_operation(
            settings.acceptance_lease_id, method.__name__, namespace, project_id,
            request_sha256=operation_request_sha256(method, self, *args, **kwargs),
        )
        token = _active_operation.set((*identity, method.__name__))
        try:
            return method(self, *args, **kwargs)
        finally:
            _active_operation.reset(token)

    return wrapped
