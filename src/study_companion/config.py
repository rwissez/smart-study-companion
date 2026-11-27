"""Environment-backed settings.

Paths are resolved against the repository root so that every entry point
(`uvicorn`, `alembic`, `python -m study_companion.cli`, `pytest`) behaves the
same regardless of the current working directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    #: 本地模拟身份入口；与前端数据来源（VITE_DATA_SOURCE）是两个独立维度。
    demo_mode: bool = True
    database_url: str = "sqlite:///./data/app.sqlite3"
    data_root: Path = Path("./data")
    output_root: Path = Path("./outputs")

    model_provider: str = ""
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = Field(default="", repr=False)
    model_timeout_seconds: int = 45

    #: 后台任务执行器。测试可通过环境变量关闭，改为手动 drain。
    jobs_worker_enabled: bool = True
    jobs_idle_seconds: float = 0.8

    #: 上传与导入限制（超过即 413）。
    max_upload_bytes: int = 32 * 1024 * 1024
    max_upload_files: int = 12
    max_import_rows: int = 200_000

    #: 外部模型外发策略：默认未获授权时不发送赛事原文。
    allow_external_model_input: bool = False

    @field_validator("data_root", "output_root", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @property
    def model_configured(self) -> bool:
        return bool(self.model_provider and self.model_base_url and self.model_name)

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        path = Path(raw)
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @property
    def blobs_root(self) -> Path:
        return self.data_root / "blobs"

    @property
    def snapshots_root(self) -> Path:
        return self.data_root / "snapshots"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test hook: drop the cached settings after mutating the environment."""
    get_settings.cache_clear()
