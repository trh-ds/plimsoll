import json
import subprocess
from pathlib import Path

import pytest

from plimsoll.checks import checkov as ck
from plimsoll.core.gate import CheckContext, CheckExecutionError, Gate
from plimsoll.core.models import CheckConfig, CheckSource, Severity

FIXTURES = Path(__file__).parent / "fixtures" / "checkov"
CORPUS = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def by_rule(findings, rule_id: str):
    return next(f for f in findings if f.rule_id == rule_id)


def test_insecure_fixture_maps_every_failed_check():
    findings = ck.parse_checkov_output(load("insecure_s3"))

    assert len(findings) == 17
    assert {f.source for f in findings} == {CheckSource.CHECKOV}
    assert all(f.file == Path("main.tf") for f in findings)


def test_mapped_checks_get_their_class_and_severity():
    findings = ck.parse_checkov_output(load("insecure_s3"))

    public_acl = by_rule(findings, "CKV_AWS_20")
    assert public_acl.rule_class == "s3.acl.public"
    assert public_acl.severity is Severity.CRITICAL
    assert public_acl.resource_address == "aws_s3_bucket.reports"
    assert public_acl.resource_type == "aws_s3_bucket"

    wildcard = by_rule(findings, "CKV_AWS_63")
    assert wildcard.rule_class == "iam.action.wildcard"
    assert wildcard.severity is Severity.HIGH
    assert (wildcard.line_start, wildcard.line_end) == (60, 74)


def test_severity_never_comes_from_the_tool():
    """Community-edition checkov reports null severity on every finding."""
    payload = load("insecure_s3")
    assert all(c["severity"] is None for c in payload["results"]["failed_checks"])
    assert all(f.severity is not None for f in ck.parse_checkov_output(payload))


def test_unmapped_checks_fall_back_below_the_default_threshold():
    lifecycle = by_rule(ck.parse_checkov_output(load("compliant_s3")), "CKV2_AWS_61")

    assert lifecycle.rule_class == "checkov.ckv2_aws_61"
    assert lifecycle.severity is Severity.MEDIUM
    assert lifecycle.severity.meets(Severity.HIGH) is False


def test_compliant_fixture_raises_nothing_the_rule_pack_covers():
    """Everything left on the compliant config is outside the v0.1 scope."""
    findings = ck.parse_checkov_output(load("compliant_s3"))

    assert findings, "checkov's broader defaults still fire here"
    assert all(f.rule_class.startswith("checkov.") for f in findings)


def test_clean_run_has_no_results_key_at_all():
    payload = load("nothing_found")

    assert "results" not in payload
    assert ck.parse_checkov_output(payload) == []


def test_multiple_frameworks_arrive_as_a_list():
    payload = load("multi_framework")
    assert isinstance(payload, list)

    findings = ck.parse_checkov_output(payload)
    sources = {f.rule_id for f in findings}
    assert "CKV_SECRET_2" in sources

    secret = by_rule(findings, "CKV_SECRET_2")
    assert secret.rule_class == "secret.hardcoded"
    assert secret.severity is Severity.CRITICAL
    # The secrets framework puts a SHA-1 of the match in `resource`, not an address.
    assert secret.resource_address == ""
    assert secret.resource_type == ""


def test_compact_output_carries_no_credential_material():
    """--compact drops code_block, which is where the credential would be."""
    raw = (FIXTURES / "multi_framework.json").read_text(encoding="utf-8")

    assert "AKIA" not in raw
    assert all(
        entry["code_block"] is None
        for block in json.loads(raw)
        for entry in block["results"]["failed_checks"]
    )


@pytest.mark.parametrize(
    ("file_path", "expected"),
    [
        ("/main.tf", Path("main.tf")),
        ("\\main.tf", Path("main.tf")),
        ("/modules/s3/main.tf", Path("modules/s3/main.tf")),
        ("\\modules\\s3\\main.tf", Path("modules/s3/main.tf")),
        ("", Path(".")),
    ],
)
def test_relative_path_normalisation(file_path, expected):
    assert ck._relative(file_path) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"file_line_range": [3, 9]}, (3, 9)),
        ({"file_line_range": [3]}, (0, 0)),
        ({"file_line_range": None}, (0, 0)),
        ({}, (0, 0)),
        ({"file_line_range": ["a", "b"]}, (0, 0)),
    ],
)
def test_line_range(entry, expected):
    assert ck._line_range(entry) == expected


def test_findings_in_one_file_have_distinct_ids():
    findings = ck.parse_checkov_output(load("insecure_s3"))
    assert len({f.id for f in findings}) == len(findings)


def test_non_list_failed_checks_is_an_adapter_failure():
    with pytest.raises(CheckExecutionError):
        ck.parse_checkov_output(
            {"check_type": "terraform", "results": {"failed_checks": {"CKV_AWS_1": 1}}}
        )


def test_non_object_result_block_is_an_adapter_failure():
    with pytest.raises(CheckExecutionError):
        ck.parse_checkov_output(["not an object"])


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [("3.3.15\n", "3.3.15"), ("3.3.15\nextra\n", "3.3.15"), ("", None), ("   ", None)],
)
def test_parse_version(stdout, expected):
    assert ck.parse_version(stdout) == expected


def test_unavailable_when_checkov_is_not_installed(monkeypatch):
    monkeypatch.setattr(ck.shutil, "which", lambda _: None)
    availability = ck.CheckovCheck().is_available()

    assert availability.available is False
    assert "PATH" in (availability.reason or "")


def test_broken_console_script_is_unavailable_not_a_crash(monkeypatch):
    """A shim on PATH pointing at an interpreter without the package. Seen in the wild."""
    monkeypatch.setattr(ck.shutil, "which", lambda _: "/usr/bin/checkov")
    monkeypatch.setattr(
        ck.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="ModuleNotFoundError: No module named 'checkov'"
        ),
    )
    availability = ck.CheckovCheck().is_available()

    assert availability.available is False
    assert "No module named" in (availability.reason or "")


def test_available_reports_version(monkeypatch):
    monkeypatch.setattr(ck.shutil, "which", lambda _: "/usr/bin/checkov")
    monkeypatch.setattr(
        ck.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="3.3.15\n", stderr=""),
    )
    availability = ck.CheckovCheck().is_available()

    assert (availability.available, availability.version) == (True, "3.3.15")


def test_run_parses_stdout(monkeypatch, tmp_path):
    payload = (FIXTURES / "insecure_s3.json").read_text(encoding="utf-8")
    monkeypatch.setattr(ck.shutil, "which", lambda _: "/usr/bin/checkov")
    monkeypatch.setattr(
        ck.subprocess,
        # Exit code 1 means findings were reported, which is the case we care about.
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout=payload, stderr=""),
    )
    findings = ck.CheckovCheck().run(CheckContext(tmp_path, CheckConfig()))
    assert len(findings) == 17


def test_run_passes_the_flags_that_keep_credentials_out_of_the_output(monkeypatch, tmp_path):
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs: object) -> subprocess.CompletedProcess:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"passed": 0}', stderr="")

    monkeypatch.setattr(ck.shutil, "which", lambda _: "/usr/bin/checkov")
    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    ck.CheckovCheck().run(CheckContext(tmp_path, CheckConfig()))

    assert "--compact" in seen[0]
    assert "--quiet" in seen[0]


def test_run_raises_when_output_is_not_json(monkeypatch, tmp_path):
    monkeypatch.setattr(ck.shutil, "which", lambda _: "/usr/bin/checkov")
    monkeypatch.setattr(
        ck.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 2, stdout="", stderr="usage: checkov"),
    )
    with pytest.raises(CheckExecutionError, match="did not return JSON"):
        ck.CheckovCheck().run(CheckContext(tmp_path, CheckConfig()))


def test_gate_skips_the_adapter_on_a_machine_without_checkov(monkeypatch, tmp_path):
    monkeypatch.setattr(ck.shutil, "which", lambda _: None)
    result = Gate([ck.CheckovCheck()]).run(tmp_path)

    assert result.any_check_ran is False
    assert [s.name for s in result.checks_skipped] == ["checkov"]
    assert result.checks_failed == []


@pytest.mark.slow
def test_against_real_checkov():
    """Shells out to a real checkov against the committed corpus."""
    import shutil as real_shutil

    if real_shutil.which("checkov") is None:
        pytest.skip("checkov not installed")

    check = ck.CheckovCheck()
    if not check.is_available().available:
        pytest.skip("checkov on PATH but not executable")

    findings = check.run(CheckContext(CORPUS / "insecure_s3", CheckConfig(timeout_seconds=600)))
    classes = {f.rule_class for f in findings}
    assert "s3.acl.public" in classes
    assert "iam.action.wildcard" in classes
