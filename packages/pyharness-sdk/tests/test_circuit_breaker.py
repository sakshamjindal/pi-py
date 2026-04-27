"""Unit tests for WebFetchCircuitBreaker."""

from __future__ import annotations

from pyharness import CircuitOpen, WebFetchCircuitBreaker


def test_clean_check_returns_none():
    """No prior failures ⇒ no breaker open."""

    b = WebFetchCircuitBreaker()
    b.advance_turn()
    assert b.check("web_fetch") is None


def test_failures_below_threshold_do_not_open():
    """N-1 failures must not open the breaker."""

    b = WebFetchCircuitBreaker(threshold=3)
    b.advance_turn()
    b.record_failure("web_fetch")
    b.advance_turn()
    b.record_failure("web_fetch")
    b.advance_turn()
    assert b.check("web_fetch") is None


def test_threshold_failures_open_breaker():
    """Hitting the threshold opens the breaker and check returns
    a CircuitOpen with a synthetic message."""

    b = WebFetchCircuitBreaker(threshold=3, cooldown_turns=5)
    b.advance_turn()
    for _ in range(3):
        b.record_failure("web_fetch")
    b.advance_turn()
    open_state = b.check("web_fetch")
    assert isinstance(open_state, CircuitOpen)
    assert open_state.tool_name == "web_fetch"
    assert open_state.failures_seen == 3
    assert open_state.turns_remaining > 0
    assert "circuit breaker" in open_state.synthetic_content
    assert "web_fetch" in open_state.synthetic_content


def test_success_resets_counter_before_open():
    """A successful call before the threshold resets the counter."""

    b = WebFetchCircuitBreaker(threshold=3)
    b.advance_turn()
    b.record_failure("web_fetch")
    b.record_failure("web_fetch")
    b.record_success("web_fetch")
    b.advance_turn()
    # Two more failures should NOT reach threshold (counter reset to 0).
    b.record_failure("web_fetch")
    b.record_failure("web_fetch")
    b.advance_turn()
    assert b.check("web_fetch") is None


def test_breaker_resets_after_cooldown():
    """Cooldown expiry resets the breaker — next check passes."""

    b = WebFetchCircuitBreaker(threshold=2, cooldown_turns=3)
    b.advance_turn()
    b.record_failure("web_fetch")
    b.record_failure("web_fetch")
    # Now open. Advance through cooldown.
    for _ in range(4):
        b.advance_turn()
    # Cooldown expired, breaker should reset on check.
    assert b.check("web_fetch") is None


def test_breaker_only_watches_specified_tools():
    """bash, read, edit etc. must never open a breaker no matter
    how many failures they accumulate."""

    b = WebFetchCircuitBreaker(threshold=2)
    b.advance_turn()
    b.record_failure("bash")
    b.record_failure("bash")
    b.record_failure("bash")
    assert b.check("bash") is None


def test_per_tool_independence():
    """web_search failures don't open web_fetch's breaker, and
    vice versa. Each watched tool has its own state."""

    b = WebFetchCircuitBreaker(threshold=2)
    b.advance_turn()
    b.record_failure("web_fetch")
    b.record_failure("web_fetch")
    # web_fetch open, web_search untouched.
    assert isinstance(b.check("web_fetch"), CircuitOpen)
    assert b.check("web_search") is None
