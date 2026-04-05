"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# Configure logging FIRST so all subsequent imports emit visible log lines
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

from app.database import init_db, is_database_available
from app.routers import auth, cases, chat, diagnosis, documents, workflow
# Import tracing after logging is configured — registers OTel provider before any LLM client
from app.services import tracing_service  # noqa: F401
from app.services.cache_service import cache_service, get_global_rate_limiter


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add HIPAA-aligned security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection for older browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Don't send referrer to external origins
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Restrict browser features
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        # Content Security Policy — tightened for PHI pages
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        # HSTS — only in production (requires HTTPS)
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        # Never cache PHI responses
        if request.url.path.startswith(("/case", "/chat", "/diagnosis", "/documents")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
    logger.info("Starting MedicalRAG API (env=%s)", settings.app_env)
    logger.info(
        "Tracing: okahu_key=%s monocle_exporter=%s service=%s",
        "set" if settings.okahu_api_key else "MISSING",
        "set" if __import__("os").environ.get("MONOCLE_EXPORTER") else "MISSING",
        settings.okahu_service_name,
    )
    yield
    logger.info("Shutting down MedicalRAG API")


app = FastAPI(
    title="Self-Reflective RAG Clinical Diagnosis",
    description="AI-assisted differential diagnosis with self-critique and evidence linking.",
    version="0.1.0",
    lifespan=lifespan,
    # Disable OpenAPI in production to avoid exposing PHI endpoints
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url="/redoc" if settings.app_env != "production" else None,
)

# Security headers — must be added before CORS
app.add_middleware(SecurityHeadersMiddleware)

_cors_origins = (
    ["*"] if settings.app_env == "development"
    else [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    if hasattr(settings, "allowed_origins") and settings.allowed_origins
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(cases.router)
app.include_router(diagnosis.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(workflow.router)


@app.get("/health")
async def health():
    db_available = await is_database_available()
    return {
        "status": "ok" if db_available else "degraded",
        "env": settings.app_env,
        "database": "online" if db_available else "offline",
    }


@app.get("/cache/stats")
async def cache_stats():
    """Monitoring endpoint — cache sizes and global daily quota remaining."""
    return {
        "caches": cache_service.stats(),
        "global_rate_limit": get_global_rate_limiter().stats(),
    }


@app.get("/")
async def ui():
    return FileResponse("frontend/login.html")


@app.get("/login")
async def login_page():
    return FileResponse("frontend/login.html")


@app.get("/signup")
async def signup_page():
    return FileResponse("frontend/signup.html")


@app.get("/app")
async def app_page():
    return FileResponse("frontend/index.html")
