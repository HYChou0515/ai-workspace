"""#242 — rendering a person into a label the LLM can read."""

from workspace_app.users import User, display_handle, speaker_label
from workspace_app.users.labels import speaker_note


def test_handle_is_the_email_local_part():
    assert display_handle(User(id="u1", name="Alice", email="alice.chen@acme.test")) == "alice.chen"


def test_handle_falls_back_to_id_without_a_usable_email():
    assert display_handle(User(id="u1", name="Alice")) == "u1"
    # A malformed email (no '@') is ignored in favour of the stable id.
    assert display_handle(User(id="u1", name="Alice", email="not-an-email")) == "u1"


def test_speaker_label_pairs_name_with_handle():
    user = User(id="u1", name="Alice Chen", email="alice.chen@acme.test")
    assert speaker_label(user) == "Alice Chen (alice.chen)"


def test_speaker_label_collapses_when_name_missing_or_equal_to_handle():
    # No display name (graceful placeholder for a stale id) → just the handle.
    assert speaker_label(User(id="u1", name="", email="alice@acme.test")) == "alice"
    # Name already equals the handle (e.g. an unknown id placeholder) → no
    # redundant "u1 (u1)".
    assert speaker_label(User(id="u1", name="u1")) == "u1"


def test_speaker_note_announces_the_current_speaker_with_section():
    note = speaker_note(
        User(id="u1", name="Alice Chen", section="Reflow", email="alice.chen@acme.test")
    )
    assert "Alice Chen (alice.chen)" in note
    assert "from Reflow" in note
    assert "shared workspace" in note


def test_speaker_note_omits_section_when_absent():
    note = speaker_note(User(id="u1", name="Alice", email="alice@acme.test"))
    assert "replying to Alice (alice)." in note
    assert "from " not in note


def test_speaker_note_is_empty_without_a_resolved_speaker():
    assert speaker_note(None) == ""
