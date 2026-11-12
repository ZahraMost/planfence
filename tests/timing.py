"""Timing helpers that do not depend on how fast the machine is.

Absolute wall-clock bounds are flaky on slow or busy CI runners and, to be safe there, have to be
so generous that they miss real regressions. So the tests use two things instead:

* operation counts (how many times something is called): exact, deterministic, machine independent;
* scaling ratios: time the same code for n and 4n units of work IN THE SAME PROCESS (min of several
  runs, GC off). Linear work gives a ratio near 4, quadratic work near 16, so the limit sits between
  them. The clock is CPU time (time.process_time), NOT wall time: on a busy runner a short run can slip
  between scheduler time slices while a long run is always preempted, which inflates wall-clock ratios
  (seen with 40 busy processes on 10 CPUs). CPU time does not count the time spent waiting for a CPU.
  A ratio failure must repeat in 3 independent attempts, so scheduler noise cannot fail a test while a
  real quadratic regression (~16x every time) always does.

`hang_guard` is the only wall-clock bound left: a very generous watchdog that turns an endless hang
into a clear failure (it never measures speed).
"""

import gc
import signal
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

FACTOR = 4
LINEAR_LIMIT = 9.0  # 4x the work: linear ~4, n log n ~4.6, quadratic ~16
ATTEMPTS = 3  # a scaling check passes if ANY attempt is below the limit
# Where the CPU clock is coarse (Windows: ~16 ms) measure longer workloads.
MIN_SECONDS = 0.005 if time.get_clock_info("process_time").resolution <= 1e-5 else 0.2
HANG_SECONDS = 900  # 15 minutes: far beyond any slow runner; only catches real hangs


class HangError(AssertionError):
    """Raised by hang_guard when the guarded block runs for longer than allowed."""


def best_of(fn: Callable[[], object], repeat: int = 7, budget: float = 3.0) -> float:
    """Smallest CPU time of `repeat` runs (stops early once `budget` seconds of CPU were spent)."""
    gc.collect()
    gc.disable()
    try:
        times, spent = [], 0.0
        for _ in range(repeat):
            start = time.process_time()
            fn()
            elapsed = time.process_time() - start
            times.append(elapsed)
            spent += elapsed
            if spent > budget:
                break
        return min(times)
    finally:
        gc.enable()


def measure_scaling(make: Callable[[int], Callable[[], object]], n0: int, factor: int = FACTOR,
                    min_seconds: float = MIN_SECONDS, calibrate: bool = True) -> tuple[float, float, float, int]:
    """make(n) builds a zero-argument workload of size n (building is not timed).

    Doubles n (up to 256x) until the small workload takes at least `min_seconds`, so the measurement
    is meaningful on a fast machine, then returns (ratio, t_small, t_big, n)."""
    n = n0
    while True:
        t_small = best_of(make(n), repeat=3)
        if not calibrate or t_small >= min_seconds or n >= n0 * 256:
            break
        n *= 2
    t_small = max(best_of(make(n)), 1e-9)
    t_big = best_of(make(n * factor))
    return t_big / t_small, t_small, t_big, n


def assert_scales_linearly(make: Callable[[int], Callable[[], object]], n0: int, what: str, **kwargs: object) -> None:
    attempts = []
    for _ in range(ATTEMPTS):
        ratio, t_small, t_big, n = measure_scaling(make, n0, **kwargs)  # type: ignore[arg-type]
        if ratio < LINEAR_LIMIT:
            return
        attempts.append(f"{ratio:.1f}x (n={n}: {t_small * 1000:.1f} ms -> {n * FACTOR}: {t_big * 1000:.1f} ms CPU)")
    raise AssertionError(
        f"{what}: {FACTOR}x the input took more than {LINEAR_LIMIT:g}x the CPU time in all {ATTEMPTS} attempts: "
        f"{'; '.join(attempts)}; linear work is ~{FACTOR}x, quadratic ~{FACTOR * FACTOR}x"
    )


@contextmanager
def hang_guard(seconds: float, what: str):  # type: ignore[no-untyped-def]
    """Fail with a clear message if the block runs longer than `seconds` (a watchdog, not a speed test).
    Uses SIGALRM, so it is a no-op where that does not exist (Windows) or off the main thread."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def fire(signum, frame):  # type: ignore[no-untyped-def]
        raise HangError(f"{what} did not finish within {seconds:g} s: it is hung (this watchdog is not a speed test)")

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
