"""HTTP hardening shared by the desktop API and the phone listener.

Both apps answer only with ApiError JSON on failure, never a traceback, path,
or document text, and every response carries the same defensive headers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vaultai.config import Settings, is_local_host
from vaultai.domain import ApiError, InvalidTransition
from vaultai.repo import Problem

log = logging.getLogger("vaultai.http")

DESKTOP_CSP = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
PHONE_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "The request could not be read."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "This method is not allowed here."),
    413: ("too_large", "The request is too large."),
}


def api_error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(ApiError(code=code, message=message).model_dump(), status_code=status, headers=headers)


def desktop_hosts(settings: Settings) -> frozenset[str]:
    return frozenset(
        {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}", f"{settings.host}:{settings.port}"}
    )


def security_headers(csp: str, *, no_store: bool) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"content-security-policy", csp.encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
    ]
    if no_store:
        headers.append((b"cache-control", b"no-store"))
    return headers


def _with_headers(send: Send, extra: list[tuple[bytes, bytes]]) -> Send:
    """Add headers the route did not set itself (a route may send its own CSP, e.g. a nonce)."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            current = list(message.get("headers", []))
            present = {k.lower() for k, _ in current}
            message = {**message, "headers": current + [(k, v) for k, v in extra if k not in present]}
        await send(message)

    return wrapped


async def _run_guarded(app: ASGIApp, scope: Scope, receive: Receive, send: Send) -> None:
    started = False

    async def tracking(message: Message) -> None:
        nonlocal started
        if message["type"] == "http.response.start":
            started = True
        await send(message)

    try:
        await app(scope, receive, tracking)
    except Exception:
        log.exception("unhandled error in %s %s", scope.get("method"), scope.get("path"))
        if started:
            raise
        response = api_error(500, "internal", "Something went wrong. Details are in the local server log.")
        await response(scope, receive, send)


Rejection = Callable[[Scope], JSONResponse | None]


class Guard:
    """Pure ASGI middleware: reject early, add security headers, and turn crashes into a bare ApiError."""

    def __init__(self, app: ASGIApp, *, reject: Rejection, csp: str, no_store: Callable[[str], bool]) -> None:
        self.app = app
        self.reject = reject
        self.csp = csp
        self.no_store = no_store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = _with_headers(send, security_headers(self.csp, no_store=self.no_store(scope["path"])))
        rejection = self.reject(scope)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await _run_guarded(self.app, scope, receive, send)


def desktop_rejection(hosts: frozenset[str]) -> Rejection:
    def reject(scope: Scope) -> JSONResponse | None:
        headers = Headers(scope=scope)
        if headers.get("host", "").lower() not in hosts:
            return api_error(421, "misdirected_request", "This server only answers requests sent to its local address.")
        if scope["method"] in STATE_CHANGING and headers.get("x-vaultai") != "1":
            return api_error(403, "csrf", "State-changing requests must carry the X-VaultAI: 1 header.")
        return None

    return reject


def lan_only_rejection(scope: Scope) -> JSONResponse | None:
    client = scope.get("client")
    if client is None or not is_local_host(client[0]):
        return api_error(403, "forbidden", "Phone capture only accepts devices on the local network.")
    return None


def install_desktop_guard(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        Guard,
        reject=desktop_rejection(desktop_hosts(settings)),
        csp=DESKTOP_CSP,
        no_store=lambda path: path == "/api" or path.startswith("/api/"),
    )


def install_phone_guard(app: FastAPI) -> None:
    app.add_middleware(Guard, reject=lan_only_rejection, csp=PHONE_API_CSP, no_store=lambda path: True)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def on_problem(request: Request, exc: Problem) -> JSONResponse:
        return api_error(exc.status, exc.code, exc.message)

    @app.exception_handler(InvalidTransition)
    async def on_transition(request: Request, exc: InvalidTransition) -> JSONResponse:
        return api_error(409, "invalid_transition", "This action is not allowed for the document's current status.")

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        names = sorted({str(err["loc"][-1]) for err in exc.errors() if err.get("loc")})
        where = f": {', '.join(names)}" if names else ""
        return api_error(422, "invalid_request", f"Invalid or missing value{where}.")

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_ERRORS.get(exc.status_code, ("error", "The request failed."))
        return api_error(exc.status_code, code, message, headers=exc.headers)

READ_CHUNK = 1 << 20


async def read_capped(upload: UploadFile, limit: int) -> bytes:
    """At most limit + 1 bytes, so the caller can tell 'exactly at the limit' from 'over it' without buffering more."""
    buf = bytearray()
    while len(buf) <= limit:
        chunk = await upload.read(min(READ_CHUNK, limit + 1 - len(buf)))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


async def read_form(request: Request, *, max_files: int, too_many: Problem) -> FormData:
    try:
        return await request.form(max_files=max_files, max_fields=20)
    except StarletteHTTPException as exc:
        if "Too many files" in str(exc.detail):
            raise too_many from None
        raise
