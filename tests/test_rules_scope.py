import time

import pytest

from planguard.parser import parse_plan
from planguard.rules import Violation, glob_match, scope, stateful_protect
from tests.conftest import load_fixture, make_plan


def changes(name):
    return parse_plan(load_fixture(name)).changes


# ---- matcher ---------------------------------------------------------------

@pytest.mark.parametrize(
    "pattern, text, expected",
    [
        ("a.b", "a.b", True),
        ("a.b", "a.c", False),
        ("a.b", "A.b", False),  # case-sensitive
        ("", "", True),
        ("", "a", False),
        ("*", "", True),
        ("*", 'anything.at["all"][0]', True),
        ("a.*", "a.b.c", True),  # * crosses dots
        ("a.*", "a.", True),  # * matches zero chars
        ("a.*", "a", False),
        ('a.b["*"]', 'a.b["key.with.dots"]', True),  # * crosses quotes
        ("module.*", "module.x.aws_s3_bucket.b", True),
        ("module.x.*", "module.y.thing", False),
        ("a?c", "abc", True),
        ("a?c", "ac", False),  # ? needs exactly one char
        ("a?c", "abbc", False),
        # '[' is literal, never a character class
        ("a.b[0]", "a.b[0]", True),
        ("a.b[0]", "a.b0", False),
        ("a.b[0]", "a.b[1]", False),
        ("a.b[01]", "a.b0", False),
        ("a.b[*]", "a.b[0]", True),
        ("a.b[*]", 'a.b["k"]', True),
        ("a.b[?]", "a.b[7]", True),
        ('a.b["key"]', 'a.b["key"]', True),
        ('a.b["key"]', 'a.b["other"]', False),
        ("a.b[!0]", "a.b[1]", False),  # fnmatch negation is NOT supported
        ("*x*y*z", "axbyc", False),
        ("*x*y*z", "axbycz", True),
        ("a*", "b", False),
        ("*b", "ab", True),
        ("*b", "ba", False),
        ("a**b", "ab", True),
    ],
)
def test_glob_match(pattern, text, expected):
    assert glob_match(pattern, text) is expected


def test_glob_match_is_not_quadratic_explosive():
    start = time.perf_counter()
    assert glob_match("*a" * 12 + "b", "a" * 20000) is False
    assert time.perf_counter() - start < 2.0


# ---- scope -----------------------------------------------------------------

def test_scope_fails_outside_allowlist():
    vs = scope(changes("update"), ["aws_*"])
    assert vs == [Violation("scope", "error", "terraform_data.example", "terraform_data", "update")]


def test_scope_passes_inside_allowlist():
    assert scope(changes("update"), ["terraform_data.*"]) == []
    assert scope(changes("update"), ["other.*", "terraform_data.example"]) == []


def test_scope_ignores_noop_and_read():
    assert scope(changes("noop"), []) == []
    assert scope(changes("read"), ["terraform_data.gate"]) == []  # read entry is out of scope check
    (v,) = scope(changes("read"), [])
    assert v.address == "terraform_data.gate"


def test_scope_empty_plan_passes():
    assert scope(changes("empty"), []) == []


def test_scope_forget_is_a_change():
    assert [v.address for v in scope(changes("forget"), [])] == ["terraform_data.example"]


def test_scope_count_noop_instances_ignored_delete_flagged():
    vs = scope(changes("count"), ["terraform_data.counted[0]", "terraform_data.counted[1]"])
    assert [v.address for v in vs] == ["terraform_data.counted[2]"]


def test_scope_bracket_addresses_real_for_each():
    ok = ['terraform_data.keyed["a"]', 'terraform_data.keyed["b"]', 'terraform_data.keyed["c"]']
    assert scope(changes("for_each"), ok) == []
    assert scope(changes("for_each"), ["terraform_data.keyed[*]"]) == []
    vs = scope(changes("for_each"), ['terraform_data.keyed["a"]'])
    assert [v.address for v in vs] == ['terraform_data.keyed["b"]', 'terraform_data.keyed["c"]']


def test_scope_module_prefix():
    assert scope(changes("module"), ["module.child.*"]) == []
    assert len(scope(changes("module"), ["module.other.*"])) == 1


def test_scope_deposed_object_reported_with_deposed_key():
    plan = parse_plan(make_plan(("a.b", "a", ["delete"], "0x1")))
    (v,) = scope(plan.changes, [])
    assert v.deposed == "0x1"


# ---- stateful-protect ------------------------------------------------------

ST = ["terraform_data"]


@pytest.mark.parametrize(
    "name, label",
    [("delete", "delete"), ("replace", "delete,create"), ("replace_cbd", "create,delete"), ("forget", "forget")],
)
def test_stateful_protect_critical(name, label):
    vs = stateful_protect(changes(name), ST)
    assert vs == [Violation("stateful-protect", "critical", "terraform_data.example", "terraform_data", label)]


@pytest.mark.parametrize("name", ["create", "update", "noop", "read", "empty", "module"])
def test_stateful_protect_passes_other_actions(name):
    assert stateful_protect(changes(name), ST) == []


def test_stateful_protect_other_types_not_flagged():
    assert stateful_protect(changes("delete"), ["aws_db_instance"]) == []
    assert stateful_protect(changes("delete"), []) == []


def test_stateful_protect_exact_type_match_no_wildcards():
    assert stateful_protect(changes("delete"), ["terraform_*"]) == []
    assert stateful_protect(changes("delete"), ["Terraform_Data"]) == []


def test_stateful_protect_only_flags_listed_types_in_mixed_plan():
    plan = parse_plan(
        make_plan(
            ("aws_db_instance.db", "aws_db_instance", ["delete"]),
            ("aws_s3_bucket.b", "aws_s3_bucket", ["delete"]),
            ("aws_db_instance.new", "aws_db_instance", ["create"]),
        )
    )
    vs = stateful_protect(plan.changes, ["aws_db_instance"])
    assert [(v.address, v.severity) for v in vs] == [("aws_db_instance.db", "critical")]


# ---- documented footgun: a lone "*" allows everything -------------------------

def test_lone_star_allows_everything_and_makes_scope_meaningless():
    """README warns about this; the behaviour is intentional and must stay documented."""
    for name in ("create", "delete", "replace", "forget", "for_each", "module"):
        assert scope(changes(name), ["*"]) == []
    assert scope(changes("delete"), ["unrelated.*", "*"]) == []


def test_readme_warns_about_lone_star():
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert "lone `*`" in readme and "meaningless" in readme


def test_stateful_types_are_not_prefix_matched():
    # "terraform_data" must not be protected by listing a shorter / different type name
    for listed in ("terraform", "terraform_dat", "t", "terraform_data_x", "TERRAFORM_DATA"):
        assert stateful_protect(changes("delete"), [listed]) == [], listed
