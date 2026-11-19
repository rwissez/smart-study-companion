"""Route modules, each owning one resource."""

from study_companion.api.routes import catalog, chat, demo_sessions, health

__all__ = ["catalog", "chat", "demo_sessions", "health"]
