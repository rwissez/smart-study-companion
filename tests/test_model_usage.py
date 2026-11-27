"""模型用量对象归一化（供应商返回 pydantic Usage 时不能崩）。"""

from __future__ import annotations

from pydantic import BaseModel

from study_companion.model_service import ModelService, ModelTaskRequest


class Usage(BaseModel):
    prompt_tokens: int = 10
    completion_tokens: int = 5


class Turn:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tool_calls = []
        self.usage = Usage()


class Provider:
    name = "fake"
    model = "fake-model"

    def generate(self, messages, tools=None, response_schema=None):  # noqa: ANN001
        self.last_messages = messages
        return Turn('{"score": 1}')


class Payload(BaseModel):
    score: int


def test_usage_object_is_normalised() -> None:
    provider = Provider()
    service = ModelService(provider=provider)
    result = service.generate_structured(
        ModelTaskRequest(task_key="unit", input_payload={"x": 1}), Payload
    )
    assert result.status == "succeeded"
    assert result.value is not None and result.value.score == 1
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    user_message = provider.last_messages[-1]["content"]
    assert "response_schema" in user_message
    assert '"score"' in user_message


def test_fenced_json_is_accepted() -> None:
    from study_companion.model_service import _json_candidate

    assert _json_candidate('```json\n{"score": 2}\n```') == '{"score": 2}'
    assert _json_candidate('说明\n{"score": 2}\n结束') == '{"score": 2}'
    assert _json_candidate('{"score": 2}') == '{"score": 2}'
