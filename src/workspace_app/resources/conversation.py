from msgspec import Struct, field


class Message(Struct):
    role: str
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


class Conversation(Struct):
    workspace_id: str
    messages: list[Message] = field(default_factory=list)
