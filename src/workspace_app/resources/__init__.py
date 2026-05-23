from specstar import SpecStar

from .agent_config import AgentConfig
from .conversation import Conversation, Message
from .investigation import Investigation, Severity, Status

__all__ = [
    "AgentConfig",
    "Conversation",
    "Investigation",
    "Message",
    "Severity",
    "Status",
    "register_all",
]


def register_all(spec: SpecStar) -> None:
    spec.add_model(AgentConfig)
    spec.add_model(Investigation)
    spec.add_model(Conversation)
