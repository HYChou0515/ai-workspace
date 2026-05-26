from specstar import SpecStar

from .agent_config import AgentConfig
from .citation_event import CitationEvent
from .conversation import Conversation, Message
from .investigation import Investigation, Severity, Status
from .kb import Collection, DocChunk, KbChat, SourceDoc
from .notification import Notification

__all__ = [
    "AgentConfig",
    "CitationEvent",
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
    # investigation_id indexed so the per-investigation conversation lookup is a
    # query, not a full scan.
    spec.add_model(Conversation, indexed_fields=["investigation_id"])
    spec.add_model(Collection)
    spec.add_model(SourceDoc)
    spec.add_model(DocChunk)
    # shared_with indexed so "chats shared with me" is a contains-query (owner
    # filtering uses the built-in created_by meta index).
    spec.add_model(KbChat, indexed_fields=["shared_with"])
    # recipient indexed so "my notifications" is a query, not a full scan.
    spec.add_model(Notification, indexed_fields=["recipient"])
    spec.add_model(CitationEvent)
