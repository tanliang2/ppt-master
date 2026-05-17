"""文本大模型调用适配器。"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

import certifi

from .model_profiles import ModelProfile


class LLMClient:
    """对不同 provider 暴露统一的单轮文本生成接口。"""

    def complete(self, profile: ModelProfile, *, system: str, user: str) -> str:
        if profile.provider in {"openai", "openai_compatible", "qwen", "deepseek", "kimi"}:
            return self._openai_compatible(profile, system=system, user=user)
        if profile.provider == "anthropic":
            return self._anthropic(profile, system=system, user=user)
        if profile.provider == "gemini":
            return self._gemini(profile, system=system, user=user)
        raise ValueError(f"暂不支持的模型 provider: {profile.provider}")

    def _openai_compatible(self, profile: ModelProfile, *, system: str, user: str) -> str:
        payload = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": profile.extra.get("temperature", 0.4),
        }
        data = self._post_json(
            f"{(profile.base_url or '').rstrip('/')}/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {profile.api_key}"},
        )
        return data["choices"][0]["message"]["content"]

    def _anthropic(self, profile: ModelProfile, *, system: str, user: str) -> str:
        payload = {
            "model": profile.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": int(profile.extra.get("max_tokens", 4096)),
            "temperature": profile.extra.get("temperature", 0.4),
        }
        data = self._post_json(
            f"{(profile.base_url or '').rstrip('/')}/v1/messages",
            payload,
            headers={
                "x-api-key": profile.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        parts = data.get("content") or []
        return "".join(part.get("text", "") for part in parts if part.get("type") == "text")

    def _gemini(self, profile: ModelProfile, *, system: str, user: str) -> str:
        prompt = f"{system}\n\n{user}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        data = self._post_json(
            f"{(profile.base_url or '').rstrip('/')}/v1beta/models/{profile.model}:generateContent?key={profile.api_key}",
            payload,
            headers={},
        )
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        return "".join(part.get("text", "") for part in parts)

    @staticmethod
    def _post_json(url: str, payload: dict, *, headers: dict[str, str]) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                **headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120, context=_ssl_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            detail = f"HTTP {exc.code}"
            if body:
                detail = f"{detail}: {body[:1000]}"
            raise RuntimeError(detail) from exc


def _ssl_context() -> ssl.SSLContext:
    """使用 certifi 根证书，避免 macOS venv 缺 CA 导致 HTTPS 调用失败。"""
    return ssl.create_default_context(cafile=certifi.where())
