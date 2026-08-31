"""The OPA adapter, against recorded `opa eval` output.

Every payload here came off a real `terraform plan` → `terraform show` → `opa eval`
pipeline; see `tests/fixtures/opa/README.md` for the exact commands and versions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plimsoll.checks.opa import (
    BUNDLED_POLICY_DIR,
    OpaCheck,
    parse_opa_output,
    parse_version,
    policy_arguments,
)
from plimsoll.core.gate import CheckContext, CheckExecutionError, CheckSkipped
from plimsoll.core.models import CheckConfig, CheckSource, Severity

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "opa"


def payload(name: str) -> object:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "reports" {\n  bucket = "x"\n}\n'
        'resource "aws_s3_bucket_acl" "reports" {\n  acl = "public-read"\n}\n',
        encoding="utf-8",
    )
    return tmp_path


def test_the_recorded_insecure_run_produces_every_finding(workspace):
    findings = parse_opa_output(payload("insecure_s3"), workspace)

    classes = sorted(f.rule_class for f in findings)
    assert classes == [
        "iam.action.wildcard",
        "iam.resource.wildcard",
        "logging.cloudtrail.absent",
        "s3.acl.public",
        "s3.encryption.missing",
        "s3.logging.missing",
        "s3.public_access_block.missing",
        "s3.versioning.disabled",
    ]
    assert all(f.source is CheckSource.OPA for f in findings)


def test_severity_comes_from_the_python_table_not_the_rule(workspace):
    by_class = {f.rule_class: f for f in parse_opa_output(payload("insecure_s3"), workspace)}

    # The .rego files carry no severity at all; these come from checks/rule_map.py.
    assert by_class["s3.acl.public"].severity is Severity.CRITICAL
    assert by_class["iam.action.wildcard"].severity is Severity.HIGH
    assert by_class["s3.versioning.disabled"].severity is Severity.MEDIUM
    assert by_class["s3.acl.public"].benchmark_refs == ["CIS-AWS-2.1.4"]


def test_an_address_is_resolved_back_to_its_block(workspace):
    by_class = {f.rule_class: f for f in parse_opa_output(payload("insecure_s3"), workspace)}

    acl = by_class["s3.acl.public"]
    assert acl.file == Path("main.tf")
    assert acl.line_start == 4
    assert acl.resource_type == "aws_s3_bucket_acl"


def test_a_whole_configuration_rule_has_no_address_and_still_lands_in_a_file(workspace):
    # `logging.cloudtrail.absent` fires on something the config does not contain, so
    # there is no block to point at.
    (finding,) = [
        f
        for f in parse_opa_output(payload("insecure_s3"), workspace)
        if f.rule_class == "logging.cloudtrail.absent"
    ]
    assert finding.resource_address == ""
    assert finding.file == Path("main.tf")
    assert finding.line_start == 0


def test_a_clean_result_is_an_empty_list_not_an_undefined_query(workspace):
    # The capture proves it: `findings` is a partial set rule, always defined.
    assert parse_opa_output(payload("no_findings"), workspace) == []


def test_an_undefined_query_is_also_empty(workspace):
    # `{}` with no `result` key -- what a missing rule package looks like. Not an error
    # here; `is_available` is what refuses to run without the bundled pack.
    assert parse_opa_output(payload("undefined_query"), workspace) == []


def test_the_compliant_fixture_trips_nothing(workspace):
    # A rule that fires here is a false positive, which is what the fixture is for.
    assert parse_opa_output(payload("compliant_s3"), workspace) == []


def test_an_entry_with_no_rule_class_is_an_error(workspace):
    # Filing it under a placeholder would make it look classified when it is not.
    broken = {"result": [{"expressions": [{"value": [{"message": "x"}]}]}]}
    with pytest.raises(CheckExecutionError, match="no rule_class"):
        parse_opa_output(broken, workspace)


def test_an_unmapped_rule_class_gets_the_default_severity(workspace):
    # Someone else's --policy-dir. Reported, but not able to fail a build on its own.
    custom = {
        "result": [
            {
                "expressions": [
                    {
                        "value": [
                            {
                                "rule_class": "acme.internal.rule",
                                "resource_address": "aws_s3_bucket.reports",
                                "message": "internal policy",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    (finding,) = parse_opa_output(custom, workspace)
    assert finding.severity is Severity.MEDIUM
    assert finding.benchmark_refs == []
    assert finding.rule_id == "acme.internal.rule"


@pytest.mark.parametrize(
    "broken",
    [
        "not a mapping",
        {"result": "not a list"},
        {"result": [{"expressions": [{"value": {"not": "a list"}}]}]},
    ],
)
def test_a_malformed_payload_is_an_error(workspace, broken):
    with pytest.raises(CheckExecutionError):
        parse_opa_output(broken, workspace)


def test_parse_version_reads_the_version_line():
    assert parse_version("Version: 1.20.1\nBuild Commit: abc\n") == "1.20.1"
    assert parse_version("no version here") is None


def test_the_bundled_pack_is_always_loaded_and_never_replaced(tmp_path):
    args = policy_arguments(tmp_path)
    assert args[:2] == ["--data", str(BUNDLED_POLICY_DIR)]
    assert args[2:] == ["--data", str(tmp_path)]
    # Without a --policy-dir the pack is still there.
    assert policy_arguments(None) == ["--data", str(BUNDLED_POLICY_DIR)]


def test_the_adapter_skips_itself_when_there_is_no_plan(tmp_path):
    ctx = CheckContext(tmp_path, CheckConfig(), plan_provider=lambda _ctx: None)
    with pytest.raises(CheckSkipped, match="no terraform plan"):
        OpaCheck().run(ctx)


def test_a_plan_that_could_not_be_produced_is_a_skip_not_a_failure(tmp_path):
    def explode(_ctx: CheckContext) -> Path | None:
        raise CheckExecutionError("terraform init failed")

    ctx = CheckContext(tmp_path, CheckConfig(), plan_provider=explode)
    with pytest.raises(CheckSkipped, match="could not be produced"):
        OpaCheck().run(ctx)
