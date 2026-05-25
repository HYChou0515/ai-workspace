from specstar import SpecStar

from .agent_config import AgentConfig
from .conversation import Conversation, Message
from .investigation import Investigation, Severity, Status
from .kb import Collection, DocChunk, KbChat, SourceDoc
from .notification import Notification

__all__ = [
    "AgentConfig",
    "Collection",
    "Conversation",
    "DocChunk",
    "Investigation",
    "KbChat",
    "Message",
    "Notification",
    "Severity",
    "SourceDoc",
    "Status",
    "register_all",
]


def register_all(spec: SpecStar) -> None:
    spec.add_model(AgentConfig)
    spec.add_model(Investigation)
    spec.add_model(Conversation)
    spec.add_model(Collection)
    spec.add_model(SourceDoc)
    spec.add_model(DocChunk)
    spec.add_model(KbChat)
    spec.add_model(Notification)
