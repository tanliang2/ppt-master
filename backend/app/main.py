"""PPT Master Agent 后端入口。"""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .model_profiles import ModelProfileStore
from .paths import REPO_ROOT, RUNTIME_DIR
from .pipeline import PipelineRunner
from .schemas import (
    ExportItem,
    HealthResponse,
    JobCreateRequest,
    JobDetailResponse,
    JobResponse,
    ModelProfileSummary,
    ModelProfileTestRequest,
    ModelProfileTestResponse,
    ProjectCreateRequest,
    ProjectResponse,
    SourceImportRequest,
    SourceImportResponse,
)
from .storage import JsonStateStore


DEFAULT_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _split_env_list(value: str | None) -> list[str]:
    """读取逗号分隔的环境变量列表。"""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


app = FastAPI(title="PPT Master Agent Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_split_env_list(os.environ.get("PPT_MASTER_CORS_ORIGINS")),
    allow_origin_regex=os.environ.get("PPT_MASTER_CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
store = JsonStateStore()
profiles = ModelProfileStore()
runner = PipelineRunner(store, profiles)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", repo_root=str(REPO_ROOT))


@app.get("/api/model-profiles", response_model=list[ModelProfileSummary])
def list_model_profiles() -> list[ModelProfileSummary]:
    return profiles.list_profiles()


@app.post("/api/model-profiles/test", response_model=ModelProfileTestResponse)
def test_model_profile(request: ModelProfileTestRequest) -> ModelProfileTestResponse:
    try:
        return profiles.test(request.profile_id, live=request.live)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/projects", response_model=ProjectResponse)
def create_project(request: ProjectCreateRequest) -> ProjectResponse:
    try:
        project_path = runner.create_project(
            name=request.name,
            canvas_format=request.canvas_format,
            base_dir=request.base_dir,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectResponse(project_path=project_path, message="项目已创建")


@app.post("/api/projects/{project_name}/sources", response_model=SourceImportResponse)
def import_sources(project_name: str, request: SourceImportRequest) -> SourceImportResponse:
    project_path = _resolve_project_path(project_name)
    try:
        summary = runner.import_sources(
            project_path=str(project_path),
            items=request.items,
            mode=request.mode,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SourceImportResponse(project_path=str(project_path), summary=summary)


@app.post("/api/projects/{project_name}/sources/upload", response_model=SourceImportResponse)
def upload_sources(
    project_name: str,
    files: list[UploadFile] = File(...),
) -> SourceImportResponse:
    project_path = _resolve_project_path(project_name)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    upload_dir = _create_upload_dir(project_path)
    saved_paths: list[str] = []
    try:
        for item in files:
            target = _safe_upload_target(upload_dir, item.filename)
            with target.open("wb") as fh:
                shutil.copyfileobj(item.file, fh)
            saved_paths.append(str(target))

        summary = runner.import_sources(
            project_path=str(project_path),
            items=saved_paths,
            mode="move",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    return SourceImportResponse(project_path=str(project_path), summary=summary)


@app.post("/api/jobs", response_model=JobResponse)
def create_job(request: JobCreateRequest) -> JobResponse:
    project_path = _resolve_project_path(request.project_path)
    job = store.create_job(
        mode=request.mode,
        project_path=str(project_path),
        profile_id=request.profile_id,
    )
    thread = threading.Thread(
        target=runner.run_job,
        kwargs={"job_id": job["id"], "prompt": request.prompt},
        daemon=True,
    )
    thread.start()
    return JobResponse(**{key: value for key, value in job.items() if key != "events"})


@app.get("/api/jobs", response_model=list[JobResponse])
def list_jobs() -> list[JobResponse]:
    return [JobResponse(**{key: value for key, value in job.items() if key != "events"}) for job in store.list_jobs()]


@app.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: str) -> JobDetailResponse:
    try:
        return JobDetailResponse(**store.get_job(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/projects/{project_name}/exports", response_model=list[ExportItem])
def list_exports(project_name: str) -> list[ExportItem]:
    project_path = _resolve_project_path(project_name)
    exports_dir = project_path / "exports"
    if not exports_dir.exists():
        return []
    return [
        ExportItem(name=path.name, path=str(path), size=path.stat().st_size)
        for path in sorted(exports_dir.glob("*.pptx"))
    ]


@app.get("/api/projects/{project_name}/exports/{file_name}")
def download_export(project_name: str, file_name: str) -> FileResponse:
    project_path = _resolve_project_path(project_name)
    target = (project_path / "exports" / file_name).resolve()
    try:
        target.relative_to((project_path / "exports").resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(str(target), filename=target.name)


def _resolve_project_path(value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute():
        project_path = raw
    else:
        repo_relative = (REPO_ROOT / value).resolve()
        if repo_relative.exists():
            project_path = repo_relative
        else:
            project_path = REPO_ROOT / "projects" / value
    project_path = project_path.resolve()
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(status_code=404, detail=f"项目不存在: {value}")
    return project_path


def _create_upload_dir(project_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    upload_dir = RUNTIME_DIR / "uploads" / project_path.name / f"{timestamp}_{uuid4().hex}"
    upload_dir.mkdir(parents=True, exist_ok=False)
    return upload_dir


def _safe_upload_target(upload_dir: Path, filename: str | None) -> Path:
    raw_name = Path(filename or "source").name
    safe_name = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in raw_name).strip()
    safe_name = safe_name.strip(".") or "source"
    target = upload_dir / safe_name
    counter = 2
    while target.exists():
        target = upload_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
        counter += 1
    return target
