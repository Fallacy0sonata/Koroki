from contextvars import ContextVar

_current_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(request_id: str) -> None:
    _current_request_id.set(request_id)


def get_request_id() -> str:
    return _current_request_id.get()
