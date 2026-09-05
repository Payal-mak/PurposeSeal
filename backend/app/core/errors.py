from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for errors that should surface as a clean JSON response
    instead of an unhandled traceback."""

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str, error_code: str = "not_found") -> None:
        super().__init__(404, error_code, message)


class ForbiddenError(AppError):
    def __init__(self, message: str, error_code: str = "forbidden") -> None:
        super().__init__(403, error_code, message)


class UnauthorizedError(AppError):
    def __init__(self, message: str, error_code: str = "unauthorized") -> None:
        super().__init__(401, error_code, message)


class ConflictError(AppError):
    def __init__(self, message: str, error_code: str = "conflict") -> None:
        super().__init__(409, error_code, message)


def _format_validation_message(error: dict) -> str:
    """Turns one pydantic/FastAPI error dict into a short, readable
    sentence instead of exposing the raw `loc`/`type`/`ctx` structure to
    the frontend. `loc` typically looks like ("body", "duration_minutes")
    or ("path", "grant_id") -- the leading location kind is dropped
    since it's not meaningful to an API caller."""
    loc = [str(part) for part in error.get("loc", []) if part not in ("body", "query", "path")]
    field = ".".join(loc) if loc else "request"
    return f"{field}: {error.get('msg', 'invalid value')}"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's default handler for this returns a raw
        {"detail": [...]} payload shaped around pydantic internals --
        every other error in this API returns {"error_code", "message"}.
        This normalizes malformed/invalid request bodies, query params,
        and path parameters (e.g. a non-integer grant_id) onto the same
        contract so the frontend never needs a special case."""
        errors = exc.errors()
        message = _format_validation_message(errors[0]) if errors else "Invalid request."
        return JSONResponse(
            status_code=422,
            content={"error_code": "validation_error", "message": message},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error_code": "internal_error", "message": "An unexpected error occurred."},
        )
