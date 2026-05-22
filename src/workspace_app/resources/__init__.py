from specstar import SpecStar

from .agent_config import AgentConfig
from .conversation import Conversation, Message
from .workspace import Workspace

__all__ = [
    "AgentConfig",
    "Conversation",
    "Message",
    "Workspace",
    "register_all",
]


def register_all(spec: SpecStar) -> None:
    spec.add_model(Workspace)
    spec.add_model(AgentConfig)
    spec.add_model(Conversation)
