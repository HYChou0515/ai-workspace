"""MockUserDirectory behaviour — id resolution + reverse handle lookup (#275).

`find_by_handle` is the reverse of `get`: the agent only ever reads the
``[Name (handle)]:`` prefix (#242), so to act on a person (look up their
section / feed `mention_user` their id) it must resolve the *handle* it can see
back to the canonical record. Unknown handle → ``None`` (strict, unlike `get`,
which returns a placeholder so rendering never breaks)."""

from __future__ import annotations

from workspace_app.users import MockUserDirectory, User, display_handle


def test_find_by_handle_resolves_the_email_local_part_to_the_record():
    directory = MockUserDirectory(
        [User(id="e123", name="Alice Chen", email="alice.chen@acme.test")]
    )
    found = directory.find_by_handle("alice.chen")
    assert found is not None
    assert found.id == "e123"
    assert found.name == "Alice Chen"


def test_find_by_handle_is_symmetric_with_display_handle_when_email_is_missing():
    # No email → display_handle falls back to the id, so that fallback handle
    # must resolve too (it's what the agent would see in the prefix).
    user = User(id="bob", name="Bob Liu")
    directory = MockUserDirectory([user])
    assert display_handle(user) == "bob"
    assert directory.find_by_handle("bob") == user


def test_find_by_handle_returns_none_for_an_unknown_handle():
    directory = MockUserDirectory(
        [User(id="e123", name="Alice Chen", email="alice.chen@acme.test")]
    )
    assert directory.find_by_handle("nobody") is None
