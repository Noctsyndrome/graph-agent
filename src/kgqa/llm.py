from __future__ import annotations

import json
import re
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
            "enable_thinking": False,
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
        content = self.extract_json_text(response.content)
        return json.loads(content)

    @staticmethod
    def strip_code_fence(content: str) -> str:
        text = content.strip()
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @classmethod
    def extract_json_text(cls, content: str) -> str:
        text = cls.strip_code_fence(content)
        text = re.sub(r"^json\s*", "", text, flags=re.IGNORECASE).strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        if text.startswith("[") and text.endswith("]"):
            return text

        object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if object_match:
            return object_match.group(0)

        array_match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if array_match:
            return array_match.group(0)

        raise ValueError("LLM did not return valid JSON content.")
