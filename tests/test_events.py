from __future__ import annotations

import json

import pytest

from macagent.eventlog import EventLog, read_events
from macagent.events import SCHEMA_VERSION, Event, EventType, tool_event


def test_event_round_trip():
    event = Event(EventType.TOOL_PRE, "hook", {"tool_name": "Bash"})
    again = Event.from_dict(json.loads(json.dumps(event.to_dict())))
    assert again == event
    assert again.v == SCHEMA_VERSION
    assert event.ts.endswith("Z")


@pytest.mark.parametrize("raw", [{}, {"type": "x"}, {"type": "x", "source": "y", "data": []}])
def test_from_dict_rejects_malformed(raw):
    with pytest.raises(ValueError):
        Event.from_dict(raw)


def test_tool_event_records_peer_uid():
    event = tool_event({"tool_name": "Read"}, peer_uid=501)
    assert event.type == EventType.TOOL_PRE
    assert event.data == {"tool_name": "Read", "peer_uid": 501}


def test_log_appends_and_reads_back(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    events = [Event("t", "s", {"i": i}) for i in range(5)]
    for event in events:
        log.append(event)
    assert list(read_events(log.path)) == events
    assert [e.data["i"] for e in read_events(log.path, last=2)] == [3, 4]
    assert (log.path.stat().st_mode & 0o777) == 0o640


def test_log_rotation_keeps_bounded_backups(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", max_bytes=300, backups=2)
    for i in range(50):
        log.append(Event("t", "s", {"i": i}))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["events.jsonl", "events.jsonl.1", "events.jsonl.2"]
    assert all(p.stat().st_size <= 300 for p in tmp_path.iterdir())
    # The newest event is in the live file.
    assert list(read_events(log.path))[-1].data["i"] == 49


def test_read_skips_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    good = Event("t", "s")
    path.write_text("not json\n\n" + json.dumps(good.to_dict()) + "\n" + '{"type": 1}\n')
    assert list(read_events(path)) == [good]


def test_read_missing_file_yields_nothing(tmp_path):
    assert list(read_events(tmp_path / "missing.jsonl")) == []


def test_log_rejects_bad_limits(tmp_path):
    with pytest.raises(ValueError):
        EventLog(tmp_path / "x", max_bytes=0)
    with pytest.raises(ValueError):
        EventLog(tmp_path / "x", backups=0)
