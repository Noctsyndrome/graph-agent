from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="neo4j_password", alias="NEO4J_PASSWORD")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    kgqa_api_base_url: str = Field(default="http://localhost:8000", alias="KGQA_API_BASE_URL")
    frontend_app_url: str = Field(default="http://127.0.0.1:5173", alias="FRONTEND_APP_URL")
    neo4j_validate_with_explain: bool = Field(default=False, alias="NEO4J_VALIDATE_WITH_EXPLAIN")
    dataset_name: str = "kgqa_poc"
    schema_file: Path = ROOT / "data" / "schema.yaml"
    few_shots_file: Path = ROOT / "data" / "few_shots.yaml"
    seed_file: Path = ROOT / "data" / "seed_data.cypher"
    evaluation_file: Path = ROOT / "tests" / "test_scenarios.yaml"
    report_file: Path = ROOT / "eval" / "report.html"

    @property
    def has_llm(self) -> bool:
        invalid_api_keys = {"", "replace-me", "your-api-key"}
        return bool(self.llm_base_url and self.llm_api_key not in invalid_api_keys and self.llm_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
