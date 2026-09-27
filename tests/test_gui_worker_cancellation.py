"""Tests du contrat d’annulation du Worker."""

from concurrent.futures import CancelledError

from file_janitor.gui.workers import Worker


def test_worker_request_cancel_emits_cancelled_and_finished() -> None:
    events: list[str] = []

    def work(*, cancel_callback=None):
        assert cancel_callback is not None
        if cancel_callback():
            raise CancelledError()
        return 42

    worker = Worker(work, cancel_kwarg="cancel_callback")
    worker.signals.cancelled.connect(lambda: events.append("cancelled"))
    worker.signals.finished.connect(lambda: events.append("finished"))
    worker.signals.result.connect(lambda result: events.append("result"))
    worker.signals.error.connect(lambda error: events.append("error"))

    worker.request_cancel()
    worker.run()

    assert events == ["cancelled", "finished"]


def test_worker_cancel_remains_silent_for_window_shutdown() -> None:
    events: list[str] = []

    def work(*, cancel_callback=None):
        assert cancel_callback is not None
        if cancel_callback():
            raise CancelledError()
        return 42

    worker = Worker(work, cancel_kwarg="cancel_callback")
    worker.signals.cancelled.connect(lambda: events.append("cancelled"))
    worker.signals.finished.connect(lambda: events.append("finished"))

    worker.cancel()
    worker.run()

    assert events == []


def test_worker_can_return_partial_result_after_cancel_request() -> None:
    events: list[object] = []

    def work(*, cancel_callback=None):
        assert cancel_callback is not None
        assert cancel_callback() is True
        return {"cancelled": True, "success": 3}

    worker = Worker(
        work,
        cancel_kwarg="cancel_callback",
        return_result_on_cancel=True,
    )
    worker.signals.cancelled.connect(lambda: events.append("cancelled"))
    worker.signals.result.connect(lambda result: events.append(result))
    worker.signals.finished.connect(lambda: events.append("finished"))

    worker.request_cancel()
    worker.run()

    assert events == [{"cancelled": True, "success": 3}, "finished"]
