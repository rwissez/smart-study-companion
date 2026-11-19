"""Provider construction from settings."""

from __future__ import annotations

from study_companion.agent.provider import ModelProvider
from study_companion.agent.providers.unconfigured import UnconfiguredProvider
from study_companion.config import Settings


def build_provider(settings: Settings) -> ModelProvider:
    """Return the provider implied by the environment.

    A0 ships only the unconfigured provider. Real vendors plug in here by
    returning an adapter for ``settings.model_provider``; the rest of the loop
    never changes.
    """
    if settings.model_configured:
        # Until a vendor adapter lands, a partially or fully configured env must
        # not silently fall back to fabricated output.
        return UnconfiguredProvider()
    return UnconfiguredProvider()
