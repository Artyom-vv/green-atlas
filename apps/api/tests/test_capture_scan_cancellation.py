import pytest

from app.native_query.capture import capture_store


def test_scan_bounds_journal_reads_and_observes_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    calls = 0
    cancelled = False

    def check() -> None:
        nonlocal calls
        calls += 1
        if cancelled:
            raise InterruptedError("cancelled")

    monkeypatch.setattr(capture_store, "monotonic", lambda: now)
    poll = capture_store._scan_check(check)
    for _ in range(100_000):
        poll()
    assert calls == 1
    cancelled = True
    now += capture_store.CAPTURE_CANCEL_POLL_SECONDS
    with pytest.raises(InterruptedError, match="cancelled"):
        poll()
    assert calls == 2
