from __future__ import annotations

import json
from typing import Any

import httpx

from kgqa.config import Settings
from kgqa.models import LLMResponse


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, prompt: str, system_prompt: str = "You are a helpful assistant.") -> LLMResponse:
        if not self.settings.has_llm:
            raise RuntimeError("LLM configuration is not available.")

        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        response = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        return LLMResponse(content=str(content).strip(), raw=body)

    def generate_json(self, prompt: str, system_prompt: str) -> dict[str, Any]:
        response = self.generate(prompt=prompt, system_prompt=system_prompt)
        content = response.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        return json.loads(content)
