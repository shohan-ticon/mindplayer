"""Unit tests for the on-disk library."""
import json
import os

import pytest

from tracesnap import library


@pytest.fixture
def tmp_library(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACESNAP_HOME", str(tmp_path))
    return tmp_path


def _fake_trace(events=5, name="demo"):
    return {
        "version": "0.1",
        "trace_id": name,
        "session": {"kind": "script", "entry": "demo.py:<module>",
                    "source": "x=1\n"},
        "events": [{"seq": i, "type": "line", "line": 1} for i in range(events)],
    }


def test_add_list_get_roundtrip(tmp_library):
    assert library.list_traces() == []
    meta = library.add(_fake_trace(events=3), name="first")
    assert meta["id"]
    assert meta["name"] == "first"
    assert meta["event_count"] == 3

    listed = library.list_traces()
    assert len(listed) == 1
    assert listed[0]["id"] == meta["id"]

    meta2, trace = library.get(meta["id"])
    assert meta2["id"] == meta["id"]
    assert trace["trace_id"] == "demo"


def test_newest_first_ordering(tmp_library):
    m1 = library.add(_fake_trace(), name="one")
    m2 = library.add(_fake_trace(), name="two")
    m3 = library.add(_fake_trace(), name="three")
    listed = library.list_traces()
    assert [m["id"] for m in listed] == [m3["id"], m2["id"], m1["id"]]


def test_rename(tmp_library):
    m = library.add(_fake_trace(), name="orig")
    updated = library.rename(m["id"], "renamed")
    assert updated["name"] == "renamed"
    assert library.list_traces()[0]["name"] == "renamed"


def test_rename_unknown_returns_none(tmp_library):
    assert library.rename("nope", "x") is None


def test_delete_removes_file_and_index_entry(tmp_library):
    m = library.add(_fake_trace())
    path = library.get_path(m["id"])
    assert path is not None and path.exists()

    assert library.delete(m["id"]) is True
    assert library.list_traces() == []
    assert not path.exists()
    assert library.delete(m["id"]) is False


def test_resolve_by_path(tmp_library, tmp_path):
    raw = tmp_path / "some.json"
    raw.write_text("{}")
    id_, p = library.resolve(str(raw))
    assert id_ is None
    assert p == raw


def test_resolve_by_id(tmp_library):
    m = library.add(_fake_trace())
    id_, p = library.resolve(m["id"])
    assert id_ == m["id"]
    assert p == library.get_path(m["id"])


def test_resolve_by_unique_name(tmp_library):
    m = library.add(_fake_trace(), name="unique-name")
    id_, p = library.resolve("unique-name")
    assert id_ == m["id"]


def test_resolve_unknown(tmp_library):
    id_, p = library.resolve("nonexistent")
    assert id_ is None and p is None
