from pathlib import Path

from tram.models.events import EventKind
from tram.obs.eventlog import EventLog


def test_append_assigns_monotonic_seq(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    e1 = log.append(EventKind.PROJECT_INITIALIZED, "test", data={"a": 1})
    e2 = log.append(EventKind.GATE_EVALUATED, "test", refs={"gate": "g2"})
    assert e2.seq == e1.seq + 1 == 2


def test_seq_survives_reopen(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(EventKind.PROJECT_INITIALIZED, "test")
    log2 = EventLog(tmp_path / "events.jsonl")
    assert log2.last_seq() == 1
    e = log2.append(EventKind.PHASE_CHANGED, "test")
    assert e.seq == 2


def test_read_filters_by_kind(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(EventKind.PROJECT_INITIALIZED, "test")
    log.append(EventKind.GATE_EVALUATED, "test")
    log.append(EventKind.GATE_EVALUATED, "test")
    assert log.count() == 3
    gates = list(log.read(kind=EventKind.GATE_EVALUATED))
    assert len(gates) == 2
    assert log.contains_seq(2) and not log.contains_seq(99)


def test_malformed_lines_are_skipped(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"seq": 1, "bogus": true}\nnot-json\n', encoding="utf-8")
    log = EventLog(path)
    assert log.last_seq() == 1  # picks up the valid seq, ignores junk
    assert log.count() == 0
