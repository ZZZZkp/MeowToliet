from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from meow_toilet.app.dependencies import (
    get_dashboard_media_service,
    get_dashboard_snapshot_service,
    get_job_dispatcher,
    get_manual_operations_service,
    get_task_store,
)
from meow_toilet.config import get_settings
from meow_toilet.domain.entities import MediaTask
from meow_toilet.observability import configure_logging
from meow_toilet.services.dashboard import DashboardMediaService, DashboardSnapshotService
from meow_toilet.services.operations import ManualOperationsService

configure_logging()

app = FastAPI(title="MeowToliet 猫砂盆看板", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))

SnapshotServiceDep = Annotated[
    DashboardSnapshotService,
    Depends(get_dashboard_snapshot_service),
]
OperationsServiceDep = Annotated[
    ManualOperationsService,
    Depends(get_manual_operations_service),
]
MediaServiceDep = Annotated[
    DashboardMediaService,
    Depends(get_dashboard_media_service),
]


def _encode_operation_result(result: Any) -> Any:
    if isinstance(result, MediaTask):
        payload = jsonable_encoder(result)
        payload["task_id"] = payload["id"]
        return payload
    return jsonable_encoder(result)


@app.get("/health")
async def health() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "status": "ok",
        "debug": settings.app_debug,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    snapshot_service: SnapshotServiceDep,
) -> HTMLResponse:
    snapshot = await snapshot_service.build_snapshot()
    context = {
        "title": "MeowToliet 猫砂盆看板",
        "snapshot": snapshot,
        "task_store_backend": type(get_task_store()).__name__,
        "dispatcher_backend": type(get_job_dispatcher()).__name__,
    }
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/api/dashboard")
async def dashboard_api(
    snapshot_service: SnapshotServiceDep,
) -> dict[str, Any]:
    snapshot = await snapshot_service.build_snapshot()
    return jsonable_encoder(snapshot)


@app.get("/api/media/cover/{task_id}")
async def dashboard_cover(
    task_id: str,
    media_service: MediaServiceDep,
) -> Response:
    asset = await media_service.load_cover_asset(task_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Cover not found.")
    content, media_type = asset
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.post("/api/operations/poll")
async def poll_once(
    snapshot_service: SnapshotServiceDep,
    operations_service: OperationsServiceDep,
    source_day: str | None = None,
) -> dict[str, Any]:
    result = await operations_service.poll_once(source_day=source_day)
    snapshot = await snapshot_service.build_snapshot()
    return {
        "action": "poll",
        "result": _encode_operation_result(result),
        "snapshot": jsonable_encoder(snapshot),
    }


@app.post("/api/operations/process-next")
async def process_next_task(
    snapshot_service: SnapshotServiceDep,
    operations_service: OperationsServiceDep,
) -> dict[str, Any]:
    result = await operations_service.process_next_task()
    snapshot = await snapshot_service.build_snapshot()
    return {
        "action": "process-next",
        "result": _encode_operation_result(result),
        "snapshot": jsonable_encoder(snapshot),
    }


@app.post("/api/operations/process/{task_id}")
async def process_task(
    task_id: str,
    snapshot_service: SnapshotServiceDep,
    operations_service: OperationsServiceDep,
) -> dict[str, Any]:
    result = await operations_service.process_task(task_id)
    snapshot = await snapshot_service.build_snapshot()
    return {
        "action": "process-task",
        "result": _encode_operation_result(result),
        "snapshot": jsonable_encoder(snapshot),
    }
