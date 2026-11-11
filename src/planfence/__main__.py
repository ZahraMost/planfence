"""Process entry point: `python -m planfence` and the `planfence` console script.

Everything that can go wrong outside cli.main() ends here with exit code 2
(import failures, interrupts, unexpected SystemExit, a failing final flush of
stdout). Only the standard library is imported at module level so that a broken
planfence module cannot prevent this guard from running.
"""

import os
import sys

EXIT_ERROR = 2


def _note(message: str) -> None:
    try:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()
    except BaseException:
        pass


def _silence_stdout() -> None:
    """Point fd 1 at the null device so the interpreter's shutdown flush of a
    broken stdout cannot change the exit status (Python would exit with 120)."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except BaseException:
        pass


def run(argv: list[str] | None = None) -> int:
    """Run planfence and return an exit code that is always 0, 1 or 2."""
    try:
        from planfence import cli  # imported here so import-time failures are caught

        code = cli.main(argv)
    except BaseException as exc:  # incl. SystemExit, KeyboardInterrupt, ImportError
        _note(f"planfence: error: unexpected {type(exc).__name__}")
        code = EXIT_ERROR
    if type(code) is not int or code not in (0, 1, 2):
        _note("planfence: error: invalid exit status")
        code = EXIT_ERROR
    try:
        sys.stdout.flush()
    except BaseException:
        _silence_stdout()
        _note("planfence: error: could not write output")
        code = EXIT_ERROR
    return code


if __name__ == "__main__":
    sys.exit(run())
