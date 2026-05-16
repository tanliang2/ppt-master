"""PPT Master Agent 后端入口。"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .model_profiles import ModelProfileStore
from .paths import REPO_ROOT
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


app = FastAPI(title="PPT Master Agent Backend", version="0.1.0")
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


@app.post("/api/jobs", response_model=JobResponse)
def create_job(request: JobCreateRequest) -> JobResponse:
    project_path = _resolve_project_path(request.project_path)
    if request.mode == "agent_plan" and not request.profile_id:
        raise HTTPException(status_code=400, detail="agent_plan 任务必须指定 profile_id")
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
