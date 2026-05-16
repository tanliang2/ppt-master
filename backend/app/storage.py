"""轻量 JSON 状态存储。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .paths import STATE_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStateStore:
    """用单个 JSON 文件记录任务状态，适合本地 MVP。"""

    def __init__(self, state_path: Path = STATE_PATH) -> None:
        self.state_path = state_path
        self._lock = threading.RLock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write({"jobs": {}})

    def create_job(self, *, mode: str, project_path: str, profile_id: str | None) -> dict[str, Any]:
        now = utc_now()
        job = {
            "id": uuid4().hex,
            "mode": mode,
            "status": "queued",
            "project_path": project_path,
            "profile_id": profile_id,
            "created_at": now,
            "updated_at": now,
            "result": {},
            "error": None,
            "events": [],
        }
        with self._lock:
            data = self._read()
            data["jobs"][job["id"]] = job
            self._write(data)
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            return list(data["jobs"].values())

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            try:
                return data["jobs"][job_id]
            except KeyError as exc:
                raise KeyError(f"任务不存在: {job_id}") from exc

    def update_job(self, job_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            job = data["jobs"][job_id]
            job.update(updates)
            job["updated_at"] = utc_now()
            self._write(data)
            return job

    def append_event(self, job_id: str, message: str, *, level: str = "info") -> None:
        with self._lock:
            data = self._read()
            job = data["jobs"][job_id]
            job.setdefault("events", []).append({
                "ts": utc_now(),
                "level": level,
                "message": message,
            })
            job["updated_at"] = utc_now()
            self._write(data)

    def _read(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)

