"""Complexity checks (no absolute time bounds): linear work stays linear, nothing is done twice.

Two kinds of check, both independent of machine speed (see tests/timing.py):
* scaling ratios: 4x the input must take well under 9x the time (linear ~4, quadratic ~16);
* operation counts: how often something is called is exact and deterministic.
"""

import json
import random

import pytest

from planfence import cli
from planfence.config import parse_config
from planfence.parser import Change, load_plan, parse_plan
from planfence.rules import AddressMatcher, check, glob_match, scope
from tests.conftest import make_plan
from tests.timing import assert_scales_linearly


def make_changes(n, length=0):
    rng = random.Random(7)
    pad = "x" * length
    return [Change(f"module.m{rng.randrange(1000)}.aws_instance.r[{i}]{pad}", "aws_instance", ("update",))
            for i in range(n)]


def scope_workload(allow):
    def make(n):
        changes = make_changes(n)
        return lambda: scope(changes, allow)
    return make


# ---- scope: linear in the number of changes, for every kind of pattern ---------------------------------

PATTERN_KINDS = {
    "exact": [f"nothing.here.{i}" for i in range(200)],
    "prefix": [f"module.none{i}.*" for i in range(200)],
    "generic": [f"zz*q{i}*r" for i in range(10)],
}


@pytest.mark.parametrize("kind", sorted(PATTERN_KINDS))
def test_scope_is_linear_in_the_number_of_changes(kind):
    assert_scales_linearly(scope_workload(PATTERN_KINDS[kind]), 300, f"scope with {kind} patterns")


def test_scope_is_linear_in_the_number_of_generic_patterns():
    def make(k):
        changes = make_changes(400)
        allow = [f"zz*q{i}*r" for i in range(k)]  # never match, so every pattern is tried for every change
        return lambda: scope(changes, allow)

    assert_scales_linearly(make, 8, "scope vs the number of generic patterns")


# ---- operation counts ----------------------------------------------------------------------------------------

def test_exact_patterns_are_looked_up_never_scanned():
    class SpySet(frozenset):
        iterations = 0
        lookups = 0

        def __iter__(self):
            SpySet.iterations += 1
            return super().__iter__()

        def __contains__(self, item):
            SpySet.lookups += 1
            return super().__contains__(item)

    matcher = AddressMatcher([f"exact.{i}" for i in range(300)])
    matcher._exact = SpySet(matcher._exact)
    texts = [f"other.{i}" for i in range(500)] + ["exact.7"]
    results = [matcher.matches(t) for t in texts]
    assert results == [False] * 500 + [True]
    assert SpySet.lookups == len(texts) and SpySet.iterations == 0


def test_the_matcher_is_built_once_per_scope_call(monkeypatch):
    built = []
    original = AddressMatcher.__init__

    def counting(self, patterns):
        built.append(1)
        original(self, patterns)

    monkeypatch.setattr(AddressMatcher, "__init__", counting)
    scope(make_changes(300), ["a.*", "b.c", "x*y*z"])
    assert sum(built) == 1


def test_the_parser_evaluates_the_sort_key_once_per_entry(monkeypatch):
    evaluated = []
    original = Change.sort_key.fget

    def counting(self):
        evaluated.append(1)
        return original(self)

    monkeypatch.setattr(Change, "sort_key", property(counting))
    entries = [(f"a.r{i}", "a", ["no-op"]) for i in range(300, 0, -1)]
    parse_plan(make_plan(*entries))
    assert len(evaluated) == 300  # a comparison sort that re-evaluated keys per comparison would be far more


# ---- the whole pipeline ---------------------------------------------------------------------------------------

CONTRACT = {
    "enabled_rules": ["no-delete", "no-replace", "scope", "stateful-protect", "budget"],
    "scope": {"allow": ["module.m1*", "aws_*.x", "q?z", "*.r[7]"]},
    "stateful": {"types": ["aws_instance", "aws_db_instance"]},
    "budget": {"max_changes": 10, "max_creates_per_type": {"aws_instance": 5}},
}


def pipeline_workload(tmp_path, address_length=0, non_ascii=False):
    config = parse_config(CONTRACT)
    pad = ("é" if non_ascii else "x") * address_length

    def make(n):
        rng = random.Random(11)
        actions = [["delete"], ["create"], ["update"], ["delete", "create"], ["no-op"], ["forget"]]
        entries = [(f"module.m{rng.randrange(10 ** 6)}.aws_instance.r[{i}]{pad}", "aws_instance", rng.choice(actions))
                   for i in range(n)]
        path = tmp_path / f"plan-{n}-{address_length}.json"
        path.write_text(json.dumps(make_plan(*entries)), encoding="utf-8")

        def run():
            plan = load_plan(path)
            violations = check(plan.changes, config)
            return cli._render("text", violations, len(plan.changes))

        return run
    return make


def test_load_check_and_render_are_linear_in_the_plan_size(tmp_path):
    assert_scales_linearly(pipeline_workload(tmp_path), 150, "load + check + render vs the number of entries")


@pytest.mark.parametrize("non_ascii", [False, True], ids=["ascii", "escaped-non-ascii"])
def test_the_pipeline_is_linear_in_the_address_length(tmp_path, non_ascii):
    # 4 x 1000 = 4000 characters stays under the 4096 limit, so no calibration (calibrate=False)
    def make(length):
        return pipeline_workload(tmp_path, address_length=length, non_ascii=non_ascii)(40)

    assert_scales_linearly(make, 1000, "pipeline vs the address length", calibrate=False)


def test_glob_match_worst_case_is_linear_in_the_text_length():
    def make(n):
        text = "a" * n
        return lambda: glob_match("*a" * 12 + "b", text)

    assert glob_match("*a" * 12 + "b", "a" * 5000) is False
    assert_scales_linearly(make, 20000, "glob_match with many stars")
