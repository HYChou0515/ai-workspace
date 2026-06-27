"""In-memory UserDirectory — seeded with a handful of imagined users, used by
tests and local dev until the real company directory is wired in.

The real directory shape (id / name / section / email / photo) is mirrored
here; swapping to the real one means writing a `UserDirectory` that calls the
company system and injecting it via `create_app(users=...)`.
"""

from __future__ import annotations

from .labels import display_handle
from .protocol import User

# Seed includes "default-user" so single-user dev (no auth) still resolves.
_SEED: list[User] = [
    User("default-user", "You", "Process Eng", "you@acme.test"),
    User("alice", "Alice Chen", "Reflow", "alice@acme.test"),
    User("bob", "Bob Liu", "SMT", "bob@acme.test"),
    User("carol", "Carol Kao", "Quality", "carol@acme.test"),
    User("dan", "Dan Jensen", "Battery", "dan@acme.test"),
]


class MockUserDirectory:
    def __init__(self, users: list[User] | None = None) -> None:
        self._users = {u.id: u for u in (users if users is not None else _SEED)}

    def get(self, user_id: str) -> User:
        # Unknown id → a graceful placeholder so stale ids never break rendering.
        return self._users.get(user_id) or User(id=user_id, name=user_id)

    def find_by_handle(self, handle: str) -> User | None:
        # Match on `display_handle` so the lookup is symmetric with the handle
        # the agent reads in the `[Name (handle)]:` prefix. First match wins —
        # the directory keeps handles unique; a stale duplicate is unlikely.
        return next((u for u in self._users.values() if display_handle(u) == handle), None)

    def all_users(self) -> list[User]:
        return list(self._users.values())
