"""The exception store, and the promise that an exempted finding never reaches a model."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from conftest import INSECURE_TF, MarkerCheck, ScriptedProvider, write_workspace
from plimsoll.core.exceptions import (
    ExceptionEntry,
    ExceptionStore,
    ExceptionStoreError,
    dump_entries,
)
from plimsoll.core.gate import Gate
from plimsoll.core.loop import VerifierLoop
from plimsoll.core.models import (
    CheckSource,
    DecisionStatus,
    Finding,
    GateConfig,
    Severity,
)

TODAY = date(2026, 9, 1)

STORE_YAML = """\
version: "0.1"
exceptions:
  - rule_class: "sg.ingress.world_open"
    resource_address: "aws_security_group.public_*"
    justification: "Public ALB, intentional."
    approved_by: "trh-ds"
    approved_in: "PR #442"
    created: "2026-03-15"
    expires: "2026-09-15"
"""


def write_store(root: Path, text: str) -> Path:
    path = ExceptionStore.path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def finding(rule_class: str = "sg.ingress.world_open", address: str = "aws_security_group.web"):
    return Finding(
        source=CheckSource.OPA,
        rule_id=rule_class,
        rule_class=rule_class,
        severity=Severity.HIGH,
        resource_address=address,
        resource_type=address.split(".")[0],
        file=Path("main.tf"),
        line_start=1,
        line_end=4,
        message="open to the internet",
    )


def entry(**overrides):
    fields = {
        "rule_class": "sg.ingress.world_open",
        "resource_address": "aws_security_group.web",
        "justification": "intentional",
        "approved_by": "trh-ds",
    }
    fields.update(overrides)
    return ExceptionEntry(**fields)


def test_missing_store_is_empty_not_an_error(tmp_path):
    store = ExceptionStore.load(tmp_path)
    assert len(store) == 0
    assert store.match(finding()) is None


def test_empty_file_is_an_empty_store(tmp_path):
    write_store(tmp_path, "")
    assert len(ExceptionStore.load(tmp_path)) == 0


def test_matching_is_exact_on_rule_class_and_glob_on_address(tmp_path):
    write_store(tmp_path, STORE_YAML)
    store = ExceptionStore.load(tmp_path)

    matched = store.match(finding(address="aws_security_group.public_alb"), TODAY)
    assert matched is not None
    assert matched.approved_by == "trh-ds"
    assert matched.expired is False

    # Glob does not match, so no exemption.
    assert store.match(finding(address="aws_security_group.private"), TODAY) is None
    # Rule class is exact, never a prefix.
    assert store.match(finding("sg.ingress.world_ssh", "aws_security_group.public_alb")) is None


def test_glob_matching_is_case_sensitive():
    # fnmatch folds case on Windows; the same store must behave the same everywhere.
    store = ExceptionStore([entry(resource_address="aws_s3_bucket.Reports")])
    assert store.match(finding("sg.ingress.world_open", "aws_s3_bucket.reports")) is None


def test_an_expired_entry_does_not_exempt_but_is_still_reported():
    store = ExceptionStore([entry(expires=TODAY - timedelta(days=1))])
    ref = store.match(finding(), TODAY)
    assert ref is not None
    assert ref.expired is True


def test_an_entry_expiring_today_has_lapsed():
    store = ExceptionStore([entry(expires=TODAY)])
    ref = store.match(finding(), TODAY)
    assert ref is not None and ref.expired is True


def test_a_live_entry_wins_over_a_stale_duplicate():
    store = ExceptionStore(
        [
            entry(expires=TODAY - timedelta(days=30), approved_by="old"),
            entry(expires=TODAY + timedelta(days=30), approved_by="renewed"),
        ]
    )
    ref = store.match(finding(), TODAY)
    assert ref is not None
    assert ref.approved_by == "renewed"
    assert ref.expired is False


@pytest.mark.parametrize(
    "text",
    [
        "- just a list",
        'version: "9.9"\nexceptions: []',
        'version: "0.1"\nexceptions: {not: a list}',
        'version: "0.1"\nexceptions:\n  - rule_class: "x"\n',  # missing required fields
        'version: "0.1"\nexceptions:\n  - "a string"\n',
        'version: "0.1"\nexceptions:\n  - [1, 2\n',  # not YAML at all
    ],
)
def test_a_malformed_store_is_an_error(tmp_path, text):
    write_store(tmp_path, text)
    with pytest.raises(ExceptionStoreError):
        ExceptionStore.load(tmp_path)


def test_unknown_keys_are_rejected_rather_than_ignored(tmp_path):
    # A misspelled key that was silently dropped would look like an exemption and not be.
    write_store(
        tmp_path,
        'version: "0.1"\nexceptions:\n'
        '  - rule_class: "a.b"\n    resource_address: "r.x"\n'
        '    justification: "j"\n    approved_by: "p"\n    expiry: "2026-01-01"\n',
    )
    with pytest.raises(ExceptionStoreError):
        ExceptionStore.load(tmp_path)


def test_round_trips_through_save_and_load(tmp_path):
    original = [entry(approved_in="PR #1", created=TODAY, expires=TODAY + timedelta(days=10))]
    ExceptionStore(original).save(ExceptionStore.path_for(tmp_path))
    assert ExceptionStore.load(tmp_path).entries == original


def test_dump_omits_absent_optional_fields():
    assert "expires" not in dump_entries([entry()])


def _loop_with(store: ExceptionStore, provider):
    check = MarkerCheck()
    return (
        VerifierLoop(
            Gate([check]),
            GateConfig(fail_on=Severity.HIGH),
            provider,
            exceptions=store,
        ),
        check,
    )


def test_an_exempted_finding_never_reaches_the_provider(tmp_path):
    write_workspace(tmp_path, INSECURE_TF)
    store = ExceptionStore(
        [entry(rule_class="s3.acl.public", resource_address="aws_s3_bucket.reports")]
    )
    provider = ScriptedProvider()  # exhausted: any call at all raises
    loop, _ = _loop_with(store, provider)

    result = loop.run(tmp_path)

    exempted = result.exempted
    assert [d.finding.rule_class for d in exempted] == ["s3.acl.public"]
    assert exempted[0].exception_ref is not None
    assert provider.prompts == []
    # And it no longer blocks the run.
    assert result.unresolved_at_or_above == []


def test_an_expired_exception_is_still_attempted_and_still_blocks(tmp_path):
    write_workspace(tmp_path, INSECURE_TF)
    store = ExceptionStore(
        [
            entry(
                rule_class="s3.acl.public",
                resource_address="aws_s3_bucket.reports",
                expires=date.today() - timedelta(days=1),
            )
        ]
    )
    loop, _ = _loop_with(store, ScriptedProvider("explanation", "still broken\n"))

    result = loop.run(tmp_path)

    (decision,) = [d for d in result.decisions if d.finding.rule_class == "s3.acl.public"]
    assert decision.status is not DecisionStatus.EXEMPTED
    assert decision.exception_expired is True
    assert result.expired_exceptions == [decision]


def test_deterministic_mode_still_exempts(tmp_path):
    write_workspace(tmp_path, INSECURE_TF)
    store = ExceptionStore([entry(rule_class="s3.acl.public", resource_address="aws_s3_bucket.*")])
    loop, _ = _loop_with(store, None)

    result = loop.run(tmp_path)

    assert [d.status for d in result.decisions if d.finding.rule_class == "s3.acl.public"] == [
        DecisionStatus.EXEMPTED
    ]
