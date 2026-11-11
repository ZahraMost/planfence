"""Scope fast paths: identical semantics to glob_match, and bounded work for large inputs."""

import random
import re
import time

import pytest

from planguard.config import ConfigError, MAX_ALLOW_PATTERNS, MAX_PATTERN_LENGTH, parse_config
from planguard.parser import Change
from planguard.rules import AddressMatcher, glob_match, scope

BOUND = 5.0  # seconds; generous, the real figures are milliseconds


def naive(patterns, text):
    return any(glob_match(p, text) for p in patterns)


# ---- semantics are unchanged -------------------------------------------------

@pytest.mark.parametrize(
    "patterns, text, expected",
    [
        (["a.b"], "a.b", True),
        (["a.b"], "a.bc", False),
        (["a.b"], "A.b", False),
        (["a.b[0]"], "a.b[0]", True),
        (["a.b[0]"], "a.b0", False),
        (["a.*"], "a.b.c", True),  # prefix fast path: * crosses dots
        (["a.*"], "a.", True),
        (["a.*"], "a", False),
        (['a.b["*'], 'a.b["k"]', True),  # prefix containing quotes and brackets
        (["*"], "", True),
        (["*"], "anything", True),
        (["a**"], "abc", True),  # two stars -> generic path
        (["*a"], "bba", True),  # suffix form -> generic path
        (["*a"], "bab", False),
        (["a?"], "ab", True),  # ? -> generic path
        (["a?"], "a", False),
        (["a?*"], "abc", True),
        (["a*b"], "axxb", True),
        (["a*b"], "axxbc", False),
        (["x", "y.*", "z?"], "y.1", True),
        (["x", "y.*", "z?"], "zz", True),
        (["x", "y.*", "z?"], "x", True),
        (["x", "y.*", "z?"], "w", False),
        ([], "a", False),
    ],
)
def test_matcher_cases(patterns, text, expected):
    assert AddressMatcher(patterns).matches(text) is expected
    assert naive(patterns, text) is expected


def test_matcher_equals_glob_match_fuzz():
    rng = random.Random(1234)
    pat_alpha, txt_alpha = 'ab.*?["]', 'ab.["]'
    for _ in range(6000):
        patterns = ["".join(rng.choice(pat_alpha) for _ in range(rng.randint(0, 6))) for _ in range(rng.randint(0, 4))]
        text = "".join(rng.choice(txt_alpha) for _ in range(rng.randint(0, 8)))
        assert AddressMatcher(patterns).matches(text) == naive(patterns, text), (patterns, text)


def test_scope_results_unchanged_with_fast_paths():
    changes = [Change(f"mod.x.r{i}", "t", ("update",)) for i in range(50)] + [Change("other", "t", ("delete",))]
    vs = scope(changes, ["mod.x.*", "exact.one", "o?her"])
    assert vs == []
    vs = scope(changes, ["mod.x.*"])
    assert [v.address for v in vs] == ["other"]


# ---- timing (generous bounds) ------------------------------------------------

def make_changes(n):
    return [Change(f"module.m{i % 50}.aws_instance.r[{i}]", "aws_instance", ("update",)) for i in range(n)]


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def test_many_exact_patterns_many_changes():
    changes = make_changes(20000)
    allow = [f"nothing.here.{i}" for i in range(499)] + [c.address for c in changes[:1]]
    vs, took = timed(lambda: scope(changes, allow))
    assert len(vs) == 19999
    assert took < BOUND


def test_many_prefix_patterns_many_changes():
    changes = make_changes(20000)
    allow = [f"module.none{i}.*" for i in range(499)] + ["module.m7.*"]
    vs, took = timed(lambda: scope(changes, allow))
    assert len(vs) == 20000 - sum(1 for c in changes if c.address.startswith("module.m7."))
    assert took < BOUND


def rx_matches(pattern, text):
    """Independent reference matcher: only * and ? are wildcards, everything else is literal."""
    rx = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern)
    return re.fullmatch(rx, text, re.DOTALL) is not None


def expected_violations(changes, allow):
    return sorted(c.address for c in changes if c.is_change and not any(rx_matches(p, c.address) for p in allow))


def test_prefix_patterns_are_not_evaluated_one_by_one_in_python(monkeypatch):
    calls = []
    real = glob_match
    monkeypatch.setattr("planguard.rules.glob_match", lambda p, t: calls.append(p) or real(p, t))
    changes = make_changes(50)
    allow = ["module.m1*", "exact", "p.*"]  # prefix, exact and prefix-only patterns
    violations = scope(changes, allow)
    assert calls == []  # exact and prefix-only patterns never reach glob_match
    # ... and the fast path still gives the right answer: m1, m10..m19 are allowed, the rest violate
    assert sorted(v.address for v in violations) == expected_violations(changes, allow)
    assert 0 < len(violations) < len(changes)
    assert len(violations) == 50 - 11  # module.m1 and module.m10 .. module.m19


def test_generic_patterns_still_bounded_for_realistic_sizes():
    changes = make_changes(2000)
    allow = [f"module.m*.aws_instance.r[{i}0*" for i in range(20)] + ["*.r[1?]"]
    vs, took = timed(lambda: scope(changes, allow))
    assert took < BOUND
    expected = expected_violations(changes, allow)
    assert sorted(v.address for v in vs) == expected  # exact set, computed independently
    assert all(v.rule == "scope" for v in vs)
    # sanity: the patterns really allow some changes and reject others
    assert 0 < len(expected) < len(changes)
    assert len(expected) == 2000 - len(
        [c for c in changes if any(rx_matches(p, c.address) for p in allow)]
    )


def test_long_prefix_and_exact_patterns_at_the_limit():
    pat = "p" * (MAX_PATTERN_LENGTH - 1) + "*"
    changes = [Change("p" * 600, "t", ("update",))]
    vs, took = timed(lambda: scope(changes, [pat] * 500))
    assert vs == [] and took < BOUND


# ---- config limits -------------------------------------------------------------

def cfg(allow):
    return {"enabled_rules": ["scope"], "scope": {"allow": allow}}


def test_limits_are_the_documented_values():
    assert (MAX_ALLOW_PATTERNS, MAX_PATTERN_LENGTH) == (500, 512)


def test_500_patterns_accepted_501_rejected():
    assert len(parse_config(cfg([f"a.{i}" for i in range(500)])).allow) == 500
    with pytest.raises(ConfigError):
        parse_config(cfg([f"a.{i}" for i in range(501)]))


def test_pattern_length_512_accepted_513_rejected():
    assert parse_config(cfg(["a" * 512])).allow == ("a" * 512,)
    assert parse_config(cfg(["*" * 512])).allow == ("*" * 512,)
    with pytest.raises(ConfigError):
        parse_config(cfg(["a" * 513]))
    with pytest.raises(ConfigError):
        parse_config(cfg(["ok", "a" * 513]))


def test_length_counts_characters_not_bytes():
    assert parse_config(cfg(["é" * 512])).allow == ("é" * 512,)
    with pytest.raises(ConfigError):
        parse_config(cfg(["é" * 513]))


def test_limit_error_messages_do_not_echo_the_pattern():
    with pytest.raises(ConfigError) as ei:
        parse_config(cfg(["SECRET-PATTERN-" + "a" * 600]))
    assert "SECRET-PATTERN" not in str(ei.value)
