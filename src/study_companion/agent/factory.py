"""Provider construction from settings."""

from __future__ import annotations

from study_companion.agent.provider import ModelProvider
from study_companion.agent.providers.openai_compatible import OpenAICompatibleProvider, load_connection
from study_companion.agent.providers.unconfigured import UnconfiguredProvider
from study_companion.config import Settings


def build_provider(settings: Settings) -> ModelProvider:
    connection = load_connection(settings)
    return OpenAICompatibleProvider(connection, settings.model_timeout_seconds) if connection else UnconfiguredProvider()
