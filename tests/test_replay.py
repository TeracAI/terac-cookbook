import pytest

from adapters.replay import ReplayAdapter


@pytest.mark.parametrize("cursor", [True, -1, 0.5, 2])
def test_replay_rejects_cursor_outside_integer_schedule(cursor):
    adapter = ReplayAdapter([{"kind": "review", "feedback": {}}])

    with pytest.raises(ValueError):
        adapter.validate_progress(cursor, "succeeded", None, None)


def test_replay_accepts_cursor_schedule_boundaries():
    adapter = ReplayAdapter([{"kind": "review", "feedback": {}}])

    adapter.validate_progress(0, "working", None, None)
    adapter.validate_progress(1, "succeeded", None, None)
