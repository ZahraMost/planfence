"""The timing helpers themselves must be able to fail: they tell linear from quadratic work."""

import signal
import time

import pytest

from tests.timing import HangError, LINEAR_LIMIT, MIN_SECONDS, assert_scales_linearly, hang_guard, measure_scaling


def linear(n):
    data = list(range(n))
    return lambda: sum(x * x for x in data)


def quadratic(n):
    data = list(range(n // 8))
    return lambda: sum(1 for a in data for b in data)  # (n/8)^2 steps


def test_linear_work_is_below_the_limit():
    assert_scales_linearly(linear, 20000, "linear sample")  # any of 3 attempts below the limit


def test_quadratic_work_is_above_the_limit():
    # a real quadratic workload is above the limit in at least one of 3 independent measurements
    # (it is ~16x every time; the retry only protects this test from a freak measurement)
    assert max(measure_scaling(quadratic, 800)[0] for _ in range(3)) > LINEAR_LIMIT


def test_the_limit_sits_between_linear_and_quadratic():
    assert 4 < LINEAR_LIMIT < 16


def test_calibration_doubles_the_size_until_the_small_workload_is_long_enough(monkeypatch):
    """Deterministic (fake CPU clock: 1 microsecond per unit of work), so no timing noise is involved."""
    import tests.timing as timing

    def workload(n):
        w = lambda: None  # noqa: E731
        w.n = n
        return w

    monkeypatch.setattr(timing, "best_of", lambda fn, **kwargs: fn.n * 1e-6)
    ratio, t_small, t_big, n = measure_scaling(workload, 2000)
    assert n >= 2000 and n % 2000 == 0
    assert t_small >= MIN_SECONDS or n == 2000 * 256  # long enough, or the 256x cap
    assert n == 2000 or (n // 2) * 1e-6 < MIN_SECONDS  # and not doubled more than needed
    assert t_big == pytest.approx(n * 4 * 1e-6) and ratio == pytest.approx(4.0)


def test_calibration_can_be_switched_off(monkeypatch):
    import tests.timing as timing

    def workload(n):
        w = lambda: None  # noqa: E731
        w.n = n
        return w

    monkeypatch.setattr(timing, "best_of", lambda fn, **kwargs: fn.n * 1e-9)
    assert measure_scaling(workload, 1000, calibrate=False)[3] == 1000


needs_alarm = pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM is not available here")


@needs_alarm
def test_hang_guard_fires_on_a_hang_with_a_clear_message():
    with pytest.raises(HangError, match=r"did not finish within 0\.2 s.*hung"):
        with hang_guard(0.2, "the fake workload"):
            time.sleep(5)  # the alarm fires after 0.2 s; this is the only sleep in the suite


@needs_alarm
def test_hang_guard_is_silent_for_quick_work_and_restores_the_alarm_state():
    previous = signal.getsignal(signal.SIGALRM)
    with hang_guard(30, "quick"):
        assert sum(range(1000)) == 499500
    assert signal.getsignal(signal.SIGALRM) is previous
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0.0


def test_assert_scales_linearly_passes_linear_work_and_names_the_problem_for_quadratic_work():
    assert_scales_linearly(linear, 20000, "linear sample")
    with pytest.raises(AssertionError, match=r"quadratic sample: 4x the input took more than 9x the CPU time in all 3 attempts"):
        assert_scales_linearly(quadratic, 800, "quadratic sample", min_seconds=0.002)


def test_a_noisy_attempt_does_not_fail_the_check_but_consistent_slowness_does(monkeypatch):
    import tests.timing as timing

    results = iter([(16.0, 1, 16, 10), (15.0, 1, 15, 10), (4.0, 1, 4, 10)])  # two noisy attempts, then fine
    monkeypatch.setattr(timing, "measure_scaling", lambda *a, **k: next(results))
    timing.assert_scales_linearly(lambda n: (lambda: None), 10, "noisy")
    always = iter([(16.0, 1, 16, 10)] * 3)
    monkeypatch.setattr(timing, "measure_scaling", lambda *a, **k: next(always))
    with pytest.raises(AssertionError, match="in all 3 attempts"):
        timing.assert_scales_linearly(lambda n: (lambda: None), 10, "slow")
