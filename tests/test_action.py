"""The GitHub Action's metadata, checked against the script that implements it.

None of this runs the Action -- building the image and driving it is the `action-image`
job in `.github/workflows/integration.yml`, because a container is only correct once it
has been built. What is asserted here is the wiring, which is where this kind of thing
actually breaks and where the breakage is silent:

An input declared in `action.yml` and never read by the entrypoint does nothing, and says
nothing. An output documented and never written is read as an empty string by whatever
workflow depends on it. And a version pinned in two files drifts in one of them.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

from plimsoll.reporters.github import MARKER

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
ENTRYPOINT = ROOT / "action" / "entrypoint.sh"
DOCKERFILE = ROOT / "action" / "Dockerfile"
INTEGRATION = ROOT / ".github" / "workflows" / "integration.yml"


def load_poster():
    """`action/` is not on the path and is not part of the wheel, so import it by file."""
    spec = importlib.util.spec_from_file_location(
        "post_comment", ROOT / "action" / "post_comment.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


poster = load_poster()


@pytest.fixture(scope="module")
def action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entrypoint() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def test_every_input_is_passed_into_the_container(action):
    """The runner does not translate hyphens, so `fail-on` would arrive as `INPUT_FAIL-ON`
    and no shell could expand it. Every input is mapped by hand; this is the check that
    none was forgotten.

    Matched on the `inputs.<name>` expression rather than on the variable's name, because
    one input deliberately arrives under a different one: `github-token` becomes
    `GITHUB_TOKEN`, which is what the API client reads and what a reader expects to see.
    """
    wired = " ".join(str(value) for value in action["runs"]["env"].values())
    missing = {name for name in action["inputs"] if f"inputs.{name}" not in wired}
    assert missing == set(), f"inputs never reach the container: {sorted(missing)}"


def test_every_mapped_variable_is_read_by_the_entrypoint(action, entrypoint):
    """An input the script never reads is a promise in the documentation and nothing
    else."""
    unread = [
        name
        for name in action["runs"]["env"]
        if name.startswith("INPUT_") and name not in entrypoint
    ]
    assert unread == [], f"declared but never read: {unread}"


def test_the_entrypoint_reads_nothing_that_is_not_declared(action, entrypoint):
    """The other direction: a typo in the script silently takes the default forever."""
    used = set(re.findall(r"INPUT_[A-Z_]+", entrypoint))
    assert used - set(action["runs"]["env"]) == set()


def test_every_output_is_written_by_the_entrypoint(action, entrypoint):
    """An undocumented-but-written output is harmless. A documented-but-unwritten one is
    read as an empty string by whatever depends on it."""
    unwritten = [name for name in action["outputs"] if f'echo "{name}=' not in entrypoint]
    assert unwritten == [], f"documented but never written: {unwritten}"


def test_the_action_never_translates_the_exit_code(entrypoint):
    """Exit 3 is "no check could run" and exit 4 is "timed out". Both must reach the
    caller: a wrapper that collapsed either into 0 would report a run that verified
    nothing as a pass."""
    assert 'exit "$status"' in entrypoint
    # The one suppression there is, and it is bounded to exit 1.
    assert entrypoint.count("exit 0") == 1


@pytest.mark.parametrize(
    ("arg", "key"),
    [
        ("TERRAFORM_VERSION", "TERRAFORM_VERSION"),
        ("OPA_VERSION", "OPA_VERSION"),
        ("CHECKOV_VERSION", "CHECKOV_VERSION"),
    ],
)
def test_the_image_and_the_integration_runner_pin_the_same_versions(arg, key):
    """Two files because one is an image and the other is a runner. The Dockerfile says
    to change them together; this is what notices when only one was changed."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    workflow = yaml.safe_load(INTEGRATION.read_text(encoding="utf-8"))

    match = re.search(rf"^ARG {arg}=(.+)$", dockerfile, re.MULTILINE)
    assert match, f"{arg} is not pinned in the Dockerfile"
    assert match.group(1).strip() == str(workflow["env"][key])


def test_apply_is_not_reachable_from_the_action(entrypoint):
    """Invariant 5, at the last layer that could break it."""
    assert "apply" not in entrypoint
    assert "apply" not in DOCKERFILE.read_text(encoding="utf-8")


# --- the comment poster ------------------------------------------------------------


def test_the_poster_looks_for_the_marker_the_reporter_writes():
    """The two halves of "edit last run's comment instead of adding another" live in
    different files -- the reporter emits the marker, the poster searches for it -- and
    nothing else would notice if one of them changed."""
    assert poster.MARKER == MARKER


def test_a_pull_request_number_is_read_from_the_event(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 512}}), encoding="utf-8")
    assert poster.pull_request_number(str(event)) == 512


def test_an_issue_comment_event_carries_the_number_too(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"issue": {"number": 7}}), encoding="utf-8")
    assert poster.pull_request_number(str(event)) == 7


@pytest.mark.parametrize("payload", ['{"push": {}}', "[]", '{"pull_request": null}'])
def test_a_push_event_has_no_pull_request(tmp_path, payload):
    """A scheduled or push run has nothing to comment on, and that is not an error."""
    event = tmp_path / "event.json"
    event.write_text(payload, encoding="utf-8")
    assert poster.pull_request_number(str(event)) is None


def test_a_missing_event_file_is_not_an_error():
    assert poster.pull_request_number(None) is None
    assert poster.pull_request_number("nowhere.json") is None


def test_an_oversized_report_is_truncated_and_keeps_its_marker():
    """GitHub rejects a body over its limit outright, which would mean no comment at all
    rather than a long one -- and a truncated comment that lost the marker would be
    appended to on the next push instead of replaced."""
    body = "x" * (poster.MAX_BODY + 5_000) + MARKER
    truncated = poster.truncate(body)

    assert len(truncated) <= poster.MAX_BODY
    assert truncated.endswith(MARKER)
    assert "truncated" in truncated


def test_a_report_within_the_limit_is_left_alone():
    body = f"## Plimsoll\n\nNo findings.\n{MARKER}"
    assert poster.truncate(body) == body
