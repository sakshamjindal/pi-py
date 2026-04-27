"""Unit tests for ToolCallDedup."""

from __future__ import annotations

from pyharness import DedupHit, ToolCallDedup


def test_first_call_misses():
    """A tool call with no prior history must execute (return None)."""

    d = ToolCallDedup()
    d.advance_turn()
    assert d.check("read", {"path": "a.txt"}) is None


def test_repeat_call_hits_with_synthetic_message():
    """Same (tool, args) on a later turn returns a DedupHit whose
    synthetic message names the tool and how many turns ago."""

    d = ToolCallDedup()
    d.advance_turn()  # turn 1
    d.record("read", {"path": "a.txt"})
    d.advance_turn()  # turn 2
    d.advance_turn()  # turn 3
    hit = d.check("read", {"path": "a.txt"})
    assert isinstance(hit, DedupHit)
    assert hit.tool_name == "read"
    assert hit.turns_ago == 2
    assert "duplicate call" in hit.synthetic_content
    assert "read" in hit.synthetic_content


def test_arg_order_does_not_matter():
    """Two argument dicts that differ only in key order must collide."""

    d = ToolCallDedup()
    d.advance_turn()
    d.record("web_fetch", {"url": "https://x", "timeout": 10})
    d.advance_turn()
    hit = d.check("web_fetch", {"timeout": 10, "url": "https://x"})
    assert hit is not None


def test_different_args_do_not_collide():
    d = ToolCallDedup()
    d.advance_turn()
    d.record("read", {"path": "a.txt"})
    d.advance_turn()
    assert d.check("read", {"path": "b.txt"}) is None


def test_mutating_tools_are_never_deduped():
    """bash, edit, write must never trigger a dedup hit even if the
    args are identical — they have side effects."""

    d = ToolCallDedup()
    d.advance_turn()
    d.record("bash", {"command": "ls"})
    d.record("edit", {"path": "a", "old_str": "x", "new_str": "y"})
    d.record("write", {"path": "a", "content": "x"})
    d.advance_turn()
    assert d.check("bash", {"command": "ls"}) is None
    assert d.check("edit", {"path": "a", "old_str": "x", "new_str": "y"}) is None
    assert d.check("write", {"path": "a", "content": "x"}) is None


def test_window_evicts_old_entries():
    """Once the LRU is full, the oldest entry is evicted on the next
    record. A check for an evicted entry must miss."""

    d = ToolCallDedup(window=3)
    d.advance_turn()
    d.record("read", {"path": "a.txt"})
    d.record("read", {"path": "b.txt"})
    d.record("read", {"path": "c.txt"})
    # All three fit so far.
    assert d.check("read", {"path": "a.txt"}) is not None
    # Adding a 4th evicts whichever is oldest by LRU order. We just
    # confirmed "a.txt" via check, which moves it to the end. So "b.txt"
    # should be the eviction victim.
    d.record("read", {"path": "d.txt"})
    assert d.check("read", {"path": "b.txt"}) is None
    # The recently-checked "a.txt" survives.
    assert d.check("read", {"path": "a.txt"}) is not None


def test_recording_a_non_deduped_tool_is_a_noop():
    """Recording bash doesn't grow the LRU. Tested by checking that
    a deduped tool with the same hash doesn't accidentally collide
    (different name => different key, so this is more about confirming
    no side effects)."""

    d = ToolCallDedup(window=2)
    d.advance_turn()
    d.record("bash", {"command": "ls"})
    d.record("read", {"path": "a"})
    d.record("read", {"path": "b"})
    # Both reads must still be present (bash didn't consume LRU slots).
    assert d.check("read", {"path": "a"}) is not None
    assert d.check("read", {"path": "b"}) is not None
