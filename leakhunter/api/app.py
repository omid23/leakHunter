"""FastAPI application factory and the single error-mapping table."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from leakhunter import __version__
from leakhunter.api.auth import AuthError
from leakhunter.api.routes import router
from leakhunter.bootstrap import Services, build_services
from leakhunter.config import Settings
from leakhunter.domain.errors import InvalidTransition, RunNotFound, UnknownActionId
from leakhunter.logging_setup import configure_logging

ERROR_STATUS: dict[type[Exception], tuple[int, str]] = {
    InvalidTransition: (409, "invalid_transition"),
    RunNotFound: (404, "not_found"),
    UnknownActionId: (422, "unknown_action_id"),
}


def error_body(kind: str, message: str, **extra: object) -> dict[str, object]:
    return {"error": {"kind": kind, "message": message, **extra}}


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    services = services or build_services(settings)
    configure_logging(services.settings.log_level, services.settings.log_json)
    app = FastAPI(title="LeakHunter", version=__version__, description="Cloud cost remediation agent")
    app.state.services = services
    app.include_router(router)

    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(error_body(exc.kind, exc.message), status_code=exc.status_code)

    for exc_type, (code, kind) in ERROR_STATUS.items():

        async def _domain(_: Request, exc: Exception, code: int = code, kind: str = kind) -> JSONResponse:
            return JSONResponse(error_body(kind, str(exc)), status_code=code)

        app.add_exception_handler(exc_type, _domain)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(error_body("validation_error", "invalid request", details=details), status_code=422)

    return app
