"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, is_database_available
from app.routers import auth, cases, chat, diagnosis, documents, workflow
# Import tracing early — registers OTel provider before any OpenAI client is created
from app.services import tracing_service  # noqa: F401

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc)
    logger.info("Starting MedicalRAG API (env=%s)", settings.app_env)
    yield
    logger.info("Shutting down MedicalRAG API")


app = FastAPI(
    title="Self-Reflective RAG Clinical Diagnosis",
    description="AI-assisted differential diagnosis with self-critique and evidence linking.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
