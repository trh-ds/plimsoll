"""Every rule in the pack fires against the violating corpus, and none against the fixed one.

Marked slow because it runs the real thing: `terraform init`, `terraform plan`,
`terraform show`, and `opa eval`, against the real bundled rule pack. There is no way to
make this assertion without them, and a version of it built on recorded output would
assert that the recording still says what it said, which is not the same claim.

Two directories rather than forty. `tests/fixtures/rules/violating/` is one workspace
whose files collectively trip all twenty rule classes; `tests/fixtures/rules/compliant/`
is the same resources with every violation fixed. Terraform merges every `.tf` in a
directory into one configuration, so both plan as a unit and one `opa eval` sees
everything. That is a stronger guarantee than one directory per rule and far less
material to keep in step.

The compliant half is the one that earns its keep. A rule that fires is easy to write; a
rule that fires only when it should is the one a team leaves switched on. This corpus has
already caught one false positive -- see `device_encrypted` in `datastore.rego`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from plimsoll.checks.rule_map import PLIMSOLL
from plimsoll.core.gate import default_gate
from plimsoll.core.models import CheckSource, GateConfig, GateResult, Severity

CORPUS = Path(__file__).resolve().parent / "fixtures" / "rules"

pytestmark = pytest.mark.slow


def requires(*binaries: str) -> None:
    missing = [b for b in binaries if shutil.which(b) is None]
    if missing:
        pytest.skip(f"needs {', '.join(missing)} on PATH")


def scan(directory: Path) -> GateResult:
    requires("terraform", "opa")
    result = default_gate().run(directory, GateConfig(fail_on=Severity.HIGH))
    if not result.any_check_ran:
        pytest.skip(f"no check could run: {result.checks_skipped} {result.checks_failed}")
    return result


def rule_classes(result: GateResult) -> set[str]:
    """Every rule class the Rego pack reported, including where it was deduplicated.

    With checkov on PATH, a violation both tools flag survives as checkov's finding with
    OPA in `also_reported_by`. Counting `source` alone made the pack look silent on the
    violating corpus, and would have hidden a false positive on the compliant one.
    """
    return {
        f.rule_class
        for f in result.findings
        if f.source is CheckSource.OPA or CheckSource.OPA in f.also_reported_by
    }


@pytest.fixture(scope="module")
def violating() -> GateResult:
    return scan(CORPUS / "violating")


@pytest.fixture(scope="module")
def compliant() -> GateResult:
    return scan(CORPUS / "compliant")


def test_every_rule_class_in_the_pack_fires(violating):
    """The whole point. A rule nobody has a violating fixture for is a rule nobody has run."""
    assert set(PLIMSOLL) - rule_classes(violating) == set()


def test_no_rule_class_fires_against_the_fixed_corpus(compliant):
    """A rule that fires here is a false positive, and a scanner that flags everything
    gets switched off."""
    assert rule_classes(compliant) == set()


def test_the_corpora_declare_the_same_resources(violating, compliant):
    """The pair is only meaningful if it is the same configuration, fixed.

    Addresses differ where the fix *is* a new resource -- a public access block, a
    CloudTrail -- so this compares the violating side as a subset.
    """
    violating_addresses = {f.resource_address for f in violating.findings if f.resource_address}
    compliant_files = {p.name for p in (CORPUS / "compliant").glob("*.tf")}
    assert {p.name for p in (CORPUS / "violating").glob("*.tf")} == compliant_files
    assert violating_addresses


def test_the_violating_corpus_blocks_a_merge(violating):
    assert violating.findings_at_or_above(Severity.HIGH)


def test_the_fixed_corpus_does_not(compliant):
    # Checkov's defaults are broader than this project's scope, so it can still report
    # things here; none of them may be classified at or above the threshold.
    assert compliant.findings_at_or_above(Severity.HIGH) == []


def test_severity_comes_from_the_table_for_every_finding(violating):
    for finding in violating.findings:
        if finding.source is not CheckSource.OPA:
            continue
        assert finding.severity is PLIMSOLL[finding.rule_class].severity


def test_every_finding_resolves_to_a_real_file(violating):
    for finding in violating.findings:
        if finding.source is not CheckSource.OPA:
            continue
        assert (CORPUS / "violating" / finding.file).is_file()
