"""PyCharm 运行入口：启动 PPT Master Agent 后端。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，便于在 PyCharm 配置里开关 reload。"""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    """启动 FastAPI 服务。"""
    host = os.environ.get("PPT_MASTER_HOST", "127.0.0.1")
    port = int(os.environ.get("PPT_MASTER_PORT", "8080"))
    reload = _env_bool("PPT_MASTER_RELOAD", default=False)

    uvicorn.run(
        "backend.app.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
