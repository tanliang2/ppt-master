"""Agent 后端任务执行管线。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .llm_client import LLMClient
from .model_profiles import ModelProfile, ModelProfileStore
from .paths import REPO_ROOT, SCRIPTS_DIR
from .storage import JsonStateStore


class PipelineRunner:
    """封装现有 PPT Master 脚本，并提供可扩展的 Agent 阶段。"""

    def __init__(self, store: JsonStateStore, profiles: ModelProfileStore) -> None:
        self.store = store
        self.profiles = profiles
        self.llm = LLMClient()

    def run_job(self, job_id: str, *, prompt: str | None = None) -> None:
        job = self.store.get_job(job_id)
        self.store.update_job(job_id, status="running")
        self.store.append_event(job_id, f"开始执行任务: {job['mode']}")
        try:
            if job["mode"] == "validate":
                result = self._validate(job_id, Path(job["project_path"]))
            elif job["mode"] == "export":
                result = self._export(job_id, Path(job["project_path"]))
            elif job["mode"] == "agent_plan":
                result = self._agent_plan(
                    job_id,
                    Path(job["project_path"]),
                    profile_id=job.get("profile_id"),
                    prompt=prompt,
                )
            else:
                raise ValueError(f"未知任务模式: {job['mode']}")
            self.store.update_job(job_id, status="succeeded", result=result, error=None)
            self.store.append_event(job_id, "任务执行完成")
        except Exception as exc:  # noqa: BLE001
            self.store.update_job(job_id, status="failed", error=str(exc))
            self.store.append_event(job_id, f"任务失败: {exc}", level="error")

    def create_project(self, *, name: str, canvas_format: str, base_dir: str | None) -> str:
        args = [
            sys.executable,
            str(SCRIPTS_DIR / "project_manager.py"),
            "init",
            name,
            "--format",
            canvas_format,
        ]
        if base_dir:
            args.extend(["--dir", base_dir])
        output = self._run(args)
        for line in output.splitlines():
            if line.startswith("Project created:"):
                raw_path = Path(line.split(":", 1)[1].strip())
                if raw_path.is_absolute():
                    return str(raw_path.resolve())
                return str((REPO_ROOT / raw_path).resolve())
        raise RuntimeError(f"无法从 project_manager 输出中解析项目路径: {output}")

    def import_sources(self, *, project_path: str, items: list[str], mode: str) -> dict[str, Any]:
        args = [
            sys.executable,
            str(SCRIPTS_DIR / "project_manager.py"),
            "import-sources",
            project_path,
            *items,
            f"--{mode}",
        ]
        output = self._run(args)
        return {"stdout": output}

    def _validate(self, job_id: str, project_path: Path) -> dict[str, Any]:
        self.store.append_event(job_id, "校验项目目录结构")
        output = self._run([
            sys.executable,
            str(SCRIPTS_DIR / "project_manager.py"),
            "validate",
            str(project_path),
        ])
        return {"stdout": output}

    def _export(self, job_id: str, project_path: Path) -> dict[str, Any]:
        self.store.append_event(job_id, "拆分讲稿 notes/total.md")
        total_output = self._run([sys.executable, str(SCRIPTS_DIR / "total_md_split.py"), str(project_path)])
        self.store.append_event(job_id, "执行 SVG 后处理")
        finalize_output = self._run([sys.executable, str(SCRIPTS_DIR / "finalize_svg.py"), str(project_path)])
        self.store.append_event(job_id, "导出 PPTX")
        export_output = self._run([sys.executable, str(SCRIPTS_DIR / "svg_to_pptx.py"), str(project_path)])
        exports = self._list_exports(project_path)
        return {
            "total_md_split": total_output,
            "finalize_svg": finalize_output,
            "svg_to_pptx": export_output,
            "exports": exports,
        }

    def _agent_plan(
        self,
        job_id: str,
        project_path: Path,
        *,
        profile_id: str | None,
        prompt: str | None,
    ) -> dict[str, Any]:
        candidate_profiles = self._resolve_agent_plan_profiles(profile_id)
        self.store.append_event(
            job_id,
            "读取 sources；按顺序尝试模型: "
            + ", ".join(profile.id for profile in candidate_profiles),
        )
        source_context = self._collect_source_context(project_path)
        system = (
            "你是 PPT Master 的 Strategist 后端代理。"
            "请基于资料生成 PPT 制作计划，输出中文，必须包含：目标受众、建议页数、画布、内容结构、视觉风格、"
            "图片策略、风险点、下一步需要用户确认的八项。不要生成 SVG。"
        )
        user = (
            f"项目路径：{project_path}\n\n"
            f"用户补充要求：{prompt or '无'}\n\n"
            f"资料内容摘录：\n{source_context}"
        )
        profile, content = self._complete_with_first_available(
            job_id,
            candidate_profiles,
            system=system,
            user=user,
        )
        self.store.update_job(job_id, profile_id=profile.id)
        output_path = project_path / "agent_plan.md"
        output_path.write_text(content, encoding="utf-8")
        self.store.append_event(job_id, f"已写入 {output_path}")
        return {"agent_plan": str(output_path), "profile_id": profile.id, "model": profile.model}

    def _resolve_agent_plan_profiles(self, profile_id: str | None) -> list[ModelProfile]:
        if profile_id:
            profile = self.profiles.get(profile_id)
            if not profile.api_key:
                raise ValueError(f"环境变量 {profile.api_key_env} 未设置")
            return [profile]

        candidates = self.profiles.iter_available(role="strategist")
        if not candidates:
            candidates = self.profiles.iter_available()
        if not candidates:
            raise ValueError("没有找到已配置 API Key 的模型 profile")
        return candidates

    def _complete_with_first_available(
        self,
        job_id: str,
        profiles: list[ModelProfile],
        *,
        system: str,
        user: str,
    ) -> tuple[ModelProfile, str]:
        errors: list[str] = []
        for profile in profiles:
            try:
                self.store.append_event(job_id, f"尝试调用模型: {profile.id} ({profile.model})")
                content = self.llm.complete(profile, system=system, user=user)
                if not content.strip():
                    raise RuntimeError("模型返回内容为空")
                self.store.append_event(job_id, f"模型可用，已选择: {profile.id}")
                return profile, content
            except Exception as exc:  # noqa: BLE001
                message = f"{profile.id}: {exc}"
                errors.append(message)
                self.store.append_event(job_id, f"模型调用失败，继续尝试下一个: {message}", level="warning")
        raise RuntimeError("所有候选模型均不可用: " + " | ".join(errors))

    def _collect_source_context(self, project_path: Path, *, limit: int = 24000) -> str:
        sources_dir = project_path / "sources"
        if not sources_dir.exists():
            return "未找到 sources/ 目录。"
        chunks: list[str] = []
        total = 0
        for path in sorted(sources_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt", ".csv", ".tsv"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            remain = limit - total
            if remain <= 0:
                break
            excerpt = text[:remain]
            chunks.append(f"\n\n# Source: {path.name}\n{excerpt}")
            total += len(excerpt)
        return "".join(chunks).strip() or "sources/ 中没有可直接读取的文本资料。"

    def _run(self, args: list[str]) -> str:
        result = subprocess.run(
            args,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        if result.returncode != 0:
            raise RuntimeError(output or f"命令失败: {' '.join(args)}")
        return output

    @staticmethod
    def _list_exports(project_path: Path) -> list[dict[str, Any]]:
        exports_dir = project_path / "exports"
        if not exports_dir.exists():
            return []
        return [
            {
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
            }
            for path in sorted(exports_dir.glob("*.pptx"))
        ]
