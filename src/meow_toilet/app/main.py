from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from meow_toilet.config import get_settings

app = FastAPI(title="MeowToliet", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


@app.get("/health")
async def health() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "status": "ok",
        "debug": settings.app_debug,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = get_settings()
    context = {
        "request": request,
        "title": "MeowToliet Control Panel",
        "petkit_ready": settings.petkit_credentials_configured,
        "gemini_ready": settings.gemini_configured,
        "feishu_ready": settings.feishu_configured,
    }
    return templates.TemplateResponse("dashboard.html", context)

