"""多模型配置加载、脱敏展示与连通性检查。"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import certifi

from .paths import DEFAULT_CONFIG_PATH
from .schemas import ModelProfileSummary, ModelProfileTestResponse


PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
    "openai_compatible": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
    },
    "gemini": {
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com",
    },
    "qwen": {
        "api_key_env": "QWEN_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    "kimi": {
        "api_key_env": "KIMI_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
    },
}


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider: str
    model: str
    api_key_env: str
    base_url: str | None = None
    roles: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def summary(self) -> ModelProfileSummary:
        return ModelProfileSummary(
            id=self.id,
            provider=self.provider,
            model=self.model,
            roles=self.roles,
            api_key_env=self.api_key_env,
            api_key_present=bool(self.api_key),
            base_url=self.base_url,
        )


class ModelProfileStore:
    """从 JSON 配置文件加载可用模型 profile。"""

    def __init__(self, config_path: str | None = None) -> None:
        env_path = os.environ.get("PPT_MASTER_LLM_CONFIG")
        self.config_path = Path(config_path or env_path or DEFAULT_CONFIG_PATH)
        self._profiles = self._load_profiles()

    def list_profiles(self) -> list[ModelProfileSummary]:
        return [profile.summary() for profile in self._profiles.values()]

    def get(self, profile_id: str) -> ModelProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"模型配置不存在: {profile_id}") from exc

    def iter_available(self, *, role: str | None = None) -> list[ModelProfile]:
        """按配置顺序返回本地可用的模型 profile。

        这里的可用指已设置对应 API Key。远程连通性在真正调用时验证，
        调用失败后由任务管线继续尝试下一个 profile。
        """
        profiles: list[ModelProfile] = []
        for profile in self._profiles.values():
            if role and profile.roles and role not in profile.roles:
                continue
            if profile.api_key:
                profiles.append(profile)
        return profiles

    def test(self, profile_id: str, *, live: bool = False) -> ModelProfileTestResponse:
        profile = self.get(profile_id)
        if not profile.api_key:
            return ModelProfileTestResponse(
                profile_id=profile.id,
                ok=False,
                live=live,
                message=f"环境变量 {profile.api_key_env} 未设置",
            )
        if not live:
            return ModelProfileTestResponse(
                profile_id=profile.id,
                ok=True,
                live=False,
                message="本地配置校验通过；未发起远程请求",
            )
        return self._live_probe(profile)

    def _load_profiles(self) -> dict[str, ModelProfile]:
        if not self.config_path.exists():
            return self._env_fallback_profiles()

        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        configured = raw.get("llm_profiles") or {}
        profiles: dict[str, ModelProfile] = {}
        for profile_id, item in configured.items():
            provider = str(item.get("provider") or "").strip()
            model = str(item.get("model") or "").strip()
            if not provider or not model:
                raise ValueError(f"模型配置 {profile_id} 缺少 provider 或 model")
            defaults = PROVIDER_DEFAULTS.get(provider, {})
            profiles[profile_id] = ModelProfile(
                id=profile_id,
                provider=provider,
                model=model,
                api_key_env=str(item.get("api_key_env") or defaults.get("api_key_env") or ""),
                base_url=item.get("base_url") or self._env_value(item.get("base_url_env")) or defaults.get("base_url"),
                roles=list(item.get("roles") or []),
                extra=dict(item.get("extra") or {}),
            )
        if not profiles:
            return self._env_fallback_profiles()
        return profiles

    def _env_fallback_profiles(self) -> dict[str, ModelProfile]:
        """没有配置文件时，按常见环境变量提供可见但未必可用的默认项。"""
        return {
            "openai_default": ModelProfile(
                id="openai_default",
                provider="openai",
                model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
                api_key_env="OPENAI_API_KEY",
                base_url=os.environ.get("OPENAI_BASE_URL", PROVIDER_DEFAULTS["openai"]["base_url"]),
                roles=["strategist", "executor", "reviewer"],
            ),
            "anthropic_default": ModelProfile(
                id="anthropic_default",
                provider="anthropic",
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet"),
                api_key_env="ANTHROPIC_API_KEY",
                base_url=os.environ.get("ANTHROPIC_BASE_URL", PROVIDER_DEFAULTS["anthropic"]["base_url"]),
                roles=["strategist", "executor"],
            ),
            "qwen_default": ModelProfile(
                id="qwen_default",
                provider="qwen",
                model=os.environ.get("QWEN_MODEL", "qwen-plus"),
                api_key_env="QWEN_API_KEY",
                base_url=os.environ.get("QWEN_BASE_URL", PROVIDER_DEFAULTS["qwen"]["base_url"]),
                roles=["source_summary", "qa"],
            ),
        }

    def _live_probe(self, profile: ModelProfile) -> ModelProfileTestResponse:
        if profile.provider in {"openai", "openai_compatible", "qwen", "deepseek", "kimi"}:
            url = f"{(profile.base_url or '').rstrip('/')}/models"
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {profile.api_key}"},
                method="GET",
            )
        elif profile.provider == "anthropic":
            url = f"{(profile.base_url or '').rstrip('/')}/v1/models"
            request = urllib.request.Request(
                url,
                headers={
                    "x-api-key": profile.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="GET",
            )
        elif profile.provider == "gemini":
            url = f"{(profile.base_url or '').rstrip('/')}/v1beta/models?key={profile.api_key}"
            request = urllib.request.Request(url, method="GET")
        else:
            return ModelProfileTestResponse(
                profile_id=profile.id,
                ok=False,
                live=True,
                message=f"暂不支持 provider 的 live probe: {profile.provider}",
            )

        try:
            with urllib.request.urlopen(request, timeout=10, context=_ssl_context()) as response:
                ok = 200 <= response.status < 300
                return ModelProfileTestResponse(
                    profile_id=profile.id,
                    ok=ok,
                    live=True,
                    message=f"远程探测 HTTP {response.status}",
                )
        except urllib.error.HTTPError as exc:
            return ModelProfileTestResponse(
                profile_id=profile.id,
                ok=False,
                live=True,
                message=f"远程探测失败 HTTP {exc.code}",
            )
        except Exception as exc:  # noqa: BLE001
            return ModelProfileTestResponse(
                profile_id=profile.id,
                ok=False,
                live=True,
                message=f"远程探测失败: {exc}",
            )

    @staticmethod
    def _env_value(name: Any) -> str | None:
        if not name:
            return None
        value = os.environ.get(str(name), "").strip()
        return value or None


def _ssl_context() -> ssl.SSLContext:
    """使用 certifi 根证书，避免 macOS venv 缺 CA 导致 HTTPS 探测失败。"""
    return ssl.create_default_context(cafile=certifi.where())
