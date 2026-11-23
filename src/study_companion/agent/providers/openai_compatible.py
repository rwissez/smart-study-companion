"""Minimal adapter for services implementing Chat Completions."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx

from study_companion.agent.types import ModelTurn
from study_companion.contracts import DomainError
from study_companion.config import Settings


@dataclass(frozen=True)
class ModelConnection:
    url: str
    model_id: str
    api_key: str = field(repr=False)

    def __post_init__(self):
        try:
            parsed = urlsplit(self.url)
            valid = (
                bool(parsed.hostname) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and (
                    parsed.scheme == "https"
                    or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
                )
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise DomainError("UNPROCESSABLE", "请输入 HTTPS 模型地址；本机模型可以使用 HTTP")

    @property
    def endpoint(self) -> str:
        url = self.url.rstrip("/")
        return url if url.endswith("/chat/completions") else url + "/chat/completions"


def load_connection(settings: Settings) -> ModelConnection | None:
    path = settings.data_root / "model-connection.json"
    if path.is_file():
        return ModelConnection(**json.loads(path.read_text(encoding="utf-8")))
    if settings.model_configured and settings.model_provider in {"openai", "openai-compatible"}:
        return ModelConnection(settings.model_base_url, settings.model_name, settings.model_api_key)
    return None


def save_connection(directory: Path, connection: ModelConnection) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({"url": connection.url, "model_id": connection.model_id, "api_key": connection.api_key}, stream)
            stream.close()
            temporary.replace(directory / "model-connection.json")
        finally:
            temporary.unlink(missing_ok=True)


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(self, connection: ModelConnection, timeout: int):
        self.connection = connection
        self.timeout = timeout

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        headers = {}
        if self.connection.api_key:
            headers["Authorization"] = f"Bearer {self.connection.api_key}"
        try:
            response = httpx.post(
                self.connection.endpoint, headers=headers,
                json={"model": self.connection.model_id, "messages": messages},
                timeout=self.timeout, follow_redirects=False, trust_env=False,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("missing text")
        except httpx.HTTPStatusError as exc:
            raise DomainError("SERVICE_UNAVAILABLE", f"模型服务返回 HTTP {exc.response.status_code}。请检查地址、密钥和模型 ID。") from None
        except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError):
            raise DomainError("SERVICE_UNAVAILABLE", "无法读取模型回复。请检查连接和接口格式。") from None
        return ModelTurn(text=text)
