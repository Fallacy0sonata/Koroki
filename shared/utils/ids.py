import uuid


def new_request_id() -> str:
    """Generate a new unique request ID for tracing a pipeline execution."""
    return str(uuid.uuid4())
