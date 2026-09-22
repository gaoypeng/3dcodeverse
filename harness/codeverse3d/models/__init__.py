"""ChatModel backends (API).  Use ``get_chat_model("gemini:gemini-3.7-flash")``."""

from codeverse3d.models.base import ChatModel
from codeverse3d.models.registry import get_chat_model, parse_model_id

__all__ = ["ChatModel", "get_chat_model", "parse_model_id"]
