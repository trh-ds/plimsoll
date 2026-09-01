"""The benchmark harness's arithmetic and scenario loading.

`benchmarks/runner.py` computes the one number this project is entitled to put in its
README. A bug in `summarise` would not crash anything, would not fail a scan, and would
publish a wrong headline -- which makes it the most expensive class of bug in the
repository and the one most worth pinning down.

What is not here is a run against a live endpoint. That needs a model, and no test in
this suite makes a network call.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    """`benchmarks/` is not a package and is not shipped, so import it by file.

    Registered in `sys.modules` before it executes, because `@dataclass` looks its own
    module up by name to resolve annotations and finds nothing if it is not there yet.
    """
    spec = importlib.util.spec_from_file_location(
        "plimsoll_benchmark_runner", ROOT / "benchmarks" / "runner.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


# --- the committed scenario set ----------------------------------------------------


@pytest.fixture(scope="module")
def scenarios():
    loaded, identity = runner.load_scenarios(ROOT / "benchmarks" / "scenarios.yaml")
    return loaded, identity


def test_the_scenario_set_loads_and_identifies_itself(scenarios):
    loaded, identity = scenarios
    assert loaded
    assert identity["count"] == len(loaded)
    assert len(identity["sha256"]) == 64


def test_every_scenario_probes_a_rule_the_pack_actually_has(scenarios):
    """`expect` is documentation rather than an assertion, but documentation naming a
    rule class that does not exist is worse than none."""
    from plimsoll.checks.rule_map import PLIMSOLL

    loaded, _ = scenarios
    named = {rule for scenario in loaded for rule in scenario.expect}
    assert named - set(PLIMSOLL) == set()


def test_the_scenarios_cover_every_rule_class(scenarios):
    """A rule nothing probes is a rule the benchmark cannot say anything about."""
    from plimsoll.checks.rule_map import PLIMSOLL

    loaded, _ = scenarios
    named = {rule for scenario in loaded for rule in scenario.expect}
    assert set(PLIMSOLL) - named == set()


def test_no_scenario_mentions_a_security_control(scenarios):
    """The experiment is what a model writes when nobody asks. A prompt that says
    "encrypted" measures instruction-following instead, and would quietly inflate the
    baseline."""
    loaded, _ = scenarios
    banned = ("encrypt", "secure", "least privilege", "versioning", "private", "harden")
    for scenario in loaded:
        lowered = scenario.prompt.lower()
        assert not [word for word in banned if word in lowered], scenario.id


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "scenarios.yaml"
    path.write_text(
        "scenarios:\n  - {id: a, prompt: x}\n  - {id: a, prompt: y}\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="duplicate"):
        runner.load_scenarios(path)


def test_a_file_with_no_scenarios_is_rejected(tmp_path):
    path = tmp_path / "scenarios.yaml"
    path.write_text("name: nothing\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.load_scenarios(path)


# --- fence stripping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '```hcl\nresource "aws_s3_bucket" "a" {}\n```',
        '```\nresource "aws_s3_bucket" "a" {}\n```',
        '```terraform\nresource "aws_s3_bucket" "a" {}```',
        '  ```hcl\nresource "aws_s3_bucket" "a" {}\n```  ',
    ],
)
def test_a_fence_around_the_whole_answer_is_removed(raw):
    """The system message forbids fences and models add them anyway. Failing over one
    would score a model as unable to write Terraform when what it could not do is follow
    a formatting instruction."""
    assert runner.strip_fence(raw) == 'resource "aws_s3_bucket" "a" {}\n'


def test_unfenced_output_is_left_alone():
    assert runner.strip_fence('resource "a" "b" {}') == 'resource "a" "b" {}\n'


def test_a_fence_inside_the_file_is_not_treated_as_a_wrapper():
    body = 'resource "a" "b" {\n  x = "```"\n}'
    assert runner.strip_fence(body).strip() == body


# --- usage accounting --------------------------------------------------------------


class Scripted:
    name = "scripted"

    def __init__(self, reply: str = "x" * 40) -> None:
        self.reply = reply

    def is_available(self):
        return None

    def describe(self) -> str:
        return "scripted"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return self.reply


def test_usage_counts_both_directions():
    counting = runner.CountingProvider(Scripted("y" * 40))
    counting.complete("a" * 60, system="b" * 20)

    usage = counting.usage
    assert usage == {
        "calls": 1,
        "prompt_chars": 80,
        "completion_chars": 40,
        # Labelled an estimate wherever it is published, because chars/4 is what it is.
        "estimated_tokens": 30,
    }


# --- the arithmetic ----------------------------------------------------------------


def row(**overrides):
    base = {
        "id": "s",
        "deployable": True,
        "baseline_pass": False,
        "post_pass": True,
        "fixed": 1,
        "escalated": 0,
        "attempts": [1],
    }
    base.update(overrides)
    return base


def test_the_delta_is_the_headline():
    rows = [
        row(id="a", baseline_pass=False, post_pass=True),
        row(id="b", baseline_pass=False, post_pass=True),
        row(id="c", baseline_pass=True, post_pass=True, fixed=0, attempts=[]),
        row(id="d", baseline_pass=False, post_pass=False, fixed=0, escalated=1, attempts=[2]),
    ]
    summary = runner.summarise(rows, {"calls": 4})

    assert summary["baseline_pass_rate"] == 25.0
    assert summary["post_pass_rate"] == 75.0
    assert summary["delta"] == 50.0


def test_a_provider_error_reduces_neither_side():
    """A router that rate-limits halfway through must not silently reduce the
    denominator, and must not count as a failure it did not observe."""
    rows = [
        row(id="a", baseline_pass=False, post_pass=True),
        {"id": "b", "error": "provider returned HTTP 429"},
    ]
    summary = runner.summarise(rows, {})

    assert summary["scenarios"] == 2
    assert summary["scored"] == 1
    assert summary["errors"] == 1
    assert summary["post_pass_rate"] == 100.0


def test_mean_attempts_counts_findings_not_scenarios():
    rows = [row(id="a", attempts=[1, 3]), row(id="b", attempts=[2])]
    assert runner.summarise(rows, {})["mean_attempts_per_finding"] == 2.0


def test_escalation_rate_is_over_findings_the_model_was_asked_about():
    rows = [row(id="a", fixed=3, escalated=1, attempts=[1, 1, 1, 2])]
    assert runner.summarise(rows, {})["escalation_rate"] == 25.0


def test_nothing_scored_reports_nothing_rather_than_zero():
    """A rate of 0% and "we could not measure it" are different claims, and the second
    one must not be published as the first."""
    summary = runner.summarise([{"id": "a", "error": "no model"}], {})

    assert summary["baseline_pass_rate"] is None
    assert summary["post_pass_rate"] is None
    assert summary["delta"] is None
    assert summary["mean_attempts_per_finding"] is None


def test_an_unmeasured_rate_renders_as_not_available_rather_than_a_number():
    summary = runner.summarise([], {})
    table = runner.markdown_table(summary, {"model": "some-model"})

    assert "n/a" in table
    assert "0%" not in table
