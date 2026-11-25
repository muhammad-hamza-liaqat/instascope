"""Domain errors and their HTTP mapping."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ScraperError(Exception):
    """Instagram returned an unexpected response."""

    status_code = 502
    code = "upstream_error"
    retryable = False

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        self.message = message or (self.__class__.__doc__ or self.code).strip()
        self.upstream_status = status
        super().__init__(self.message)


class InvalidUsername(ScraperError):
    """Not a valid Instagram username."""

    status_code = 422
    code = "invalid_username"


class ProfileNotFound(ScraperError):
    """Instagram profile does not exist."""

    status_code = 404
    code = "profile_not_found"


class LoginRequired(ScraperError):
    """Instagram requires a logged-in session for this data. Set IG_SESSION_IDS."""

    status_code = 503
    code = "login_required"


class Blocked(ScraperError):
    """Instagram blocked the request (checkpoint/challenge/login wall). Use proxies or sessions."""

    status_code = 503
    code = "blocked"


class RateLimited(ScraperError):
    """Instagram rate limit hit. Slow down or add proxies/sessions."""

    status_code = 429
    code = "rate_limited"
    retryable = True


class UpstreamError(ScraperError):
    """Instagram is unreachable or returned a server error."""

    status_code = 502
    code = "upstream_error"
    retryable = True


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ScraperError)
    async def _scraper_error(_: Request, exc: ScraperError) -> JSONResponse:
        headers = {"Retry-After": "60"} if isinstance(exc, RateLimited) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": exc.message},
            headers=headers,
        )
