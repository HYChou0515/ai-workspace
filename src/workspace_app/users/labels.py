"""Rendering helpers — turn a `User` into a label for a person.

`id` is the stable key, but display names repeat (同名同姓), so the label pairs
the name with a unique, human-readable *handle* — the email local-part, the same
disambiguator Slack (`@handle`) and GitHub (`@login`) use. Format: ``Name
(handle)``. The canonical id stays out of the label (it lives in structured
context); the label is what the LLM reads to tell collaborators apart.
"""

from __future__ import annotations

from .protocol import User


def display_handle(user: User) -> str:
    """A unique, human-readable handle: the email local-part
    (``alice.chen@acme.test`` → ``alice.chen``), falling back to the stable id
    when the directory has no email."""
    if user.email and "@" in user.email:
        return user.email.split("@", 1)[0]
    return user.id


def attributed(label: str, text: str) -> str:
    """``[Name (handle)]: text`` — a person's message as the model sees it. The
    ONE spelling of the prefix: history, a message read into a running turn,
    and the message a turn answers all go through here, so they cannot drift
    apart. An empty `label` (no person wrote it) leaves `text` unchanged."""
    return f"[{label}]: {text}" if label else text


def speaker_label(user: User) -> str:
    """``Name (handle)`` — how a message is attributed to a person in the LLM's
    view. Collapses to a single token when the name is missing (a graceful
    placeholder for an unknown/stale id) or already equals the handle."""
    name = user.name.strip()
    handle = display_handle(user)
    if not name or name == handle:
        return name or handle
    return f"{name} ({handle})"


def speaker_note(user: User | None) -> str:
    """A per-turn system instruction (#242) telling the agent who, in a shared
    multi-collaborator workspace, it is replying to right now — and how to read
    the per-speaker prefixes on user messages. Empty when no speaker is
    resolved (single-user / unwired) so the base prompt is left untouched."""
    if user is None:
        return ""
    where = f" from {user.section}" if user.section else ""
    return (
        "You are in a shared workspace where teammates may message you in the "
        "same thread; each message a person wrote is prefixed with its sender as "
        f"[Name (handle)]:. You are now replying to {speaker_label(user)}{where}."
    )
