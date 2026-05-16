"""API 请求与响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    repo_root: str


class ModelProfileSummary(BaseModel):
    id: str
    provider: str
    model: str
    roles: list[str] = Field(default_factory=list)
    api_key_env: str
    api_key_present: bool
    base_url: str | None = None


class ModelProfileTestRequest(BaseModel):
    profile_id: str
    live: bool = False


class ModelProfileTestResponse(BaseModel):
    profile_id: str
    ok: bool
    live: bool
    message: str


class ProjectCreateRequest(BaseModel):
    name: str
    canvas_format: str = "ppt169"
    base_dir: str | None = None


class ProjectResponse(BaseModel):
    project_path: str
    message: str


class SourceImportRequest(BaseModel):
    items: list[str]
    mode: Literal["copy", "move"] = "copy"


class SourceImportResponse(BaseModel):
    project_path: str
    summary: dict[str, Any]


class JobCreateRequest(BaseModel):
    mode: Literal["validate", "export", "agent_plan"]
    project_path: str
    profile_id: str | None = None
    prompt: str | None = None


class JobResponse(BaseModel):
    id: str
    mode: str
    status: str
    project_path: str
    profile_id: str | None = None
    created_at: str
    updated_at: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class JobEvent(BaseModel):
    ts: str
    level: Literal["info", "warning", "error"]
    message: str


class JobDetailResponse(JobResponse):
    events: list[JobEvent] = Field(default_factory=list)


class ExportItem(BaseModel):
    name: str
    path: str
    size: int

