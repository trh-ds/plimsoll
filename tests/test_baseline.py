"""Baselines: what they suppress, what they never suppress, and how they survive edits."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from conftest import SECURE_TF, MarkerCheck, ScriptedProvider, write_workspace
from plimsoll.core.baseline import (
    Baseline,
    BaselineError,
    load_baseline,
    write_baseline,
)
from plimsoll.core.gate import Gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import (
    CheckSource,
    DecisionStatus,
    Finding,
    GateConfig,
    Severity,
    SuppressionKind,
)


def make(line: int = 3, *, address: str = "aws_s3_bucket.reports", rule: str = "s3.acl.public"):
    return Finding(
        source=CheckSource.OPA,
        rule_id="PLIM_S3_001",
        rule_class=rule,
        severity=Severity.CRITICAL,
        resource_address=address,
        resource_type="aws_s3_bucket",
        file=Path("main.tf"),
        line_start=line,
        line_end=line,
        message="public",
    )


def test_the_fingerprint_survives_the_resource_moving_down_the_file():
    """The reason `id` cannot be the key: it changes when someone adds a line above."""
    assert make(3).id != make(40).id
    assert make(3).fingerprint == make(40).fingerprint


def test_a_different_rule_or_resource_is_a_different_fingerprint():
    assert make().fingerprint != make(rule="s3.versioning.disabled").fingerprint
    assert make().fingerprint != make(address="aws_s3_bucket.other").fingerprint


def test_a_finding_with_no_address_keeps_its_line():
    # Nothing else anchors a file-level finding. Two hardcoded secrets in one file are two
    # problems, and baselining the first must not hide the second.
    assert make(3, address="").fingerprint != make(9, address="").fingerprint


def test_one_violation_reported_twice_is_one_entry_and_the_file_is_sorted():
    later = make(address="aws_s3_bucket.zeta")
    snapshot = Baseline.from_findings([later, make(), make(50)], created=date(2026, 10, 1))
    assert [e.resource_address for e in snapshot.findings] == [
        "aws_s3_bucket.reports",
        "aws_s3_bucket.zeta",
    ]


def test_a_baseline_round_trips(tmp_path):
    snapshot = Baseline.from_findings([make()], created=date(2026, 10, 1))
    path = write_baseline(snapshot, tmp_path / ".plimsoll" / "baseline.json")
    assert load_baseline(path).fingerprints == {make().fingerprint}


def test_a_missing_baseline_that_was_asked_for_is_an_error(tmp_path):
    with pytest.raises(BaselineError, match="plimsoll baseline"):
        load_baseline(tmp_path / "nope.json")


@pytest.mark.parametrize(
    "body",
    ["not json", '{"version": "9", "findings": []}', '{"findings": [{"rule_class": "x"}]}'],
)
def test_a_malformed_baseline_is_an_error(tmp_path, body):
    path = tmp_path / "baseline.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(BaselineError):
        load_baseline(path)


def test_the_gate_moves_baselined_findings_out_of_the_count_but_keeps_them(tmp_path):
    write_workspace(tmp_path)
    gate = Gate([MarkerCheck()])
    (existing,) = gate.run(tmp_path).findings

    result = gate.run(tmp_path, GateConfig(baseline=frozenset({existing.fingerprint})))

    assert result.findings == []
    assert [s.kind for s in result.suppressed] == [SuppressionKind.BASELINE]
    assert result.suppressed[0].finding.fingerprint == existing.fingerprint


def test_a_new_violation_is_not_covered_by_the_baseline(tmp_path):
    write_workspace(tmp_path)
    gate = Gate([MarkerCheck()])
    baseline = frozenset(f.fingerprint for f in gate.run(tmp_path).findings)

    (tmp_path / "net.tf").write_text(
        'resource "aws_security_group" "web" {\n  cidr = "0.0.0.0/0"\n}\n', encoding="utf-8"
    )
    result = gate.run(tmp_path, GateConfig(baseline=baseline))

    assert [f.rule_class for f in result.findings] == ["sg.ingress.world_open"]


def test_a_patch_is_reverified_under_the_same_baseline(tmp_path):
    """Invariant 2. A baselined finding still present after a patch is not a regression."""
    tf = SECURE_TF.replace('acl    = "private"', 'acl    = "public-read"')
    tf += 'resource "aws_security_group" "web" {\n  cidr = "0.0.0.0/0"\n}\n'
    write_workspace(tmp_path, tf)
    gate = Gate([MarkerCheck()])
    acl = next(f for f in gate.run(tmp_path).findings if f.rule_class == "s3.acl.public")

    fixed_sg = tf.replace('"0.0.0.0/0"', '"10.0.0.0/8"')
    loop = VerifierLoop(
        gate,
        GateConfig(baseline=frozenset({acl.fingerprint})),
        ScriptedProvider("The group is open to the world.", fixed_sg),
    )
    result = loop.run(tmp_path)

    (decision,) = result.decisions
    assert decision.finding.rule_class == "sg.ingress.world_open"
    assert decision.status is DecisionStatus.FIXED
    assert [s.finding.rule_class for s in result.gate.suppressed] == ["s3.acl.public"]


def test_the_baseline_file_is_readable_json(tmp_path):
    path = write_baseline(Baseline.from_findings([make()]), tmp_path / "b.json")
    entry = json.loads(path.read_text(encoding="utf-8"))["findings"][0]
    assert entry["rule_class"] == "s3.acl.public"
    assert entry["severity"] == "CRITICAL"
