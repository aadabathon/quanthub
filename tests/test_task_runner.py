"""Tests for the GUI task bridge (deliberately toolkit-free)."""

import time

import pytest

from quant_hub.tasks import TaskRunner


def drain(runner, predicate, timeout=5.0):
    """Poll process_results (as the GUI pump would) until predicate() holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runner.process_results()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


@pytest.fixture
def runner():
    r = TaskRunner()
    yield r
    r.shutdown()


class TestTaskRunner:
    def test_result_delivered_to_on_done(self, runner):
        results = []
        runner.submit(lambda: 40 + 2, on_done=results.append)
        drain(runner, lambda: results == [42])

    def test_exception_delivered_to_on_error(self, runner):
        errors = []

        def boom():
            raise RuntimeError("kaput")

        runner.submit(boom, on_error=errors.append)
        drain(runner, lambda: len(errors) == 1)
        assert isinstance(errors[0], RuntimeError)

    def test_jobs_complete_in_submission_order(self, runner):
        order = []
        for i in range(5):
            runner.submit(lambda i=i: i, on_done=order.append)
        drain(runner, lambda: len(order) == 5)
        assert order == [0, 1, 2, 3, 4]

    def test_callback_exception_does_not_kill_the_pump(self, runner):
        results = []

        def bad_callback(_value):
            raise ValueError("callback bug")

        runner.submit(lambda: 1, on_done=bad_callback)
        runner.submit(lambda: 2, on_done=results.append)
        drain(runner, lambda: results == [2])

    def test_shutdown_stops_worker(self):
        runner = TaskRunner()
        runner.shutdown()
        assert not runner._worker.is_alive()
