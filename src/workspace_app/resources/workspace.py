from msgspec import Struct


class Workspace(Struct):
    name: str
    description: str = ""
    attached_agent_config_id: str | None = None
