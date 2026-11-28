"""模型外发授权门：远端默认拒绝，本地与显式授权放行。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from study_companion.config import Settings
from study_companion.contracts import DomainError
from study_companion.model_service import ModelService, ModelTaskRequest, assert_external_model_allowed


def _settings(url: str, allow: bool = False) -> Settings:
    return Settings(
        model_provider="openai_compatible",
        model_base_url=url,
        model_name="test-model",
        model_api_key="test-key",
        allow_external_model_input=allow,
    )


def test_remote_model_blocked_by_default() -> None:
    with pytest.raises(DomainError) as excinfo:
        assert_external_model_allowed(_settings("https://llm.example.com/v1"))
    assert excinfo.value.code == "MODEL_EXTERNAL_NOT_ALLOWED"


def test_remote_model_allowed_with_explicit_flag() -> None:
    assert_external_model_allowed(_settings("https://llm.example.com/v1", allow=True))


def test_loopback_model_is_trusted() -> None:
    assert_external_model_allowed(_settings("http://127.0.0.1:11434/v1"))
    assert_external_model_allowed(_settings("http://localhost:8001/v1"))


def test_unconfigured_model_does_not_trigger_external_gate() -> None:
    settings = Settings(
        model_provider="",
        model_base_url="",
        model_name="",
        model_api_key="",
    )

    assert_external_model_allowed(settings)


class _Payload(BaseModel):
    value: int


class _Provider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.called = False

    def generate(self, messages, tools=None, response_schema=None):  # noqa: ANN001
        self.called = True
        return type("Turn", (), {"text": '{"value":1}', "usage": None})()


def test_task_disallowing_external_input_blocks_remote_provider_even_when_globally_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider()
    service = ModelService(settings=_settings("https://llm.example.com/v1", allow=True))
    monkeypatch.setattr(service, "_load_provider", lambda: provider)

    result = service.generate_structured(
        ModelTaskRequest(task_key="unit", allow_external=False), _Payload
    )

    assert result.status == "failed"
    assert result.error and result.error["code"] == "MODEL_EXTERNAL_NOT_ALLOWED"
    assert provider.called is False


def test_task_allowing_external_input_uses_globally_authorized_remote_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider()
    service = ModelService(settings=_settings("https://llm.example.com/v1", allow=True))
    monkeypatch.setattr(service, "_load_provider", lambda: provider)

    result = service.generate_structured(
        ModelTaskRequest(task_key="unit", allow_external=True), _Payload
    )

    assert result.status == "succeeded"
    assert provider.called is True
