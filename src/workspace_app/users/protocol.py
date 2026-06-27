"""UserDirectory Protocol — the contract for resolving user ids to people.

Users are owned by the *company directory*, not by us: we only store user ids
(on `Investigation.owner`/`members`, `KbChat.shared_with`, `Notification`,
citation events, …) and resolve them to display info (name / photo / section)
through this Protocol when rendering.

Implementations: `MockUserDirectory` (seeded, tests + local dev); a real one
later wraps the company directory. Inject via `create_app(users=...)`; nothing
else changes. The "current user" is a separate injected callable
`get_user_id()` (from the auth middleware) — `current()` simply resolves it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class User:
    """A person from the company directory. `id` is the stable key we store;
    the rest is display info resolved on read."""

    id: str
    name: str
    section: str = ""
    email: str = ""
    photo_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class UserDirectory(Protocol):
    def get(self, user_id: str) -> User:
        """Resolve a user id to a `User`. Unknown ids should still return a
        `User` (a graceful placeholder) rather than raise, so a stale id on an
        old record never breaks rendering."""
        ...

    def find_by_handle(self, handle: str) -> User | None:
        """Reverse of `get`: resolve a *handle* (the email local-part shown to
        the agent as ``[Name (handle)]:``, #242) back to its `User`, so the
        agent can act on a person it can only see by handle (#275). Strict —
        an unknown handle returns ``None`` (not a placeholder), so the caller
        can tell the agent the handle was unrecognised."""
        ...

    def all_users(self) -> list[User]:
        """Every user (the directory is small — a few hundred), so pickers can
        fetch once and filter client-side."""
        ...
