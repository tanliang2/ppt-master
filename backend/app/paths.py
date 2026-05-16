"""后端路径常量。"""

from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
SKILL_DIR = REPO_ROOT / "skills" / "ppt-master"
SCRIPTS_DIR = SKILL_DIR / "scripts"
PROJECTS_DIR = REPO_ROOT / "projects"
RUNTIME_DIR = BACKEND_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "state.json"
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config.example.json"

