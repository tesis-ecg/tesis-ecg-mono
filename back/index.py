"""Vercel FastAPI entrypoint; the application itself lives in app.main."""

from app.main import app

__all__ = ["app"]
