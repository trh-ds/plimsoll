"""`.plimsoll/config.yaml`: optional, strict about its shape, and below every flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from plimsoll.core.config import ConfigError, load_project_config
from plimsoll.core.models import Severity


def write(root: Path, body: str) -> Path:
    path = root / ".plimsoll" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_no_file_is_an_empty_config(tmp_path):
    config = load_project_config(tmp_path)
    assert config.fail_on is None and config.baseline is None


def test_an_empty_file_is_an_empty_config(tmp_path):
    write(tmp_path, "")
    assert load_project_config(tmp_path).timeout is None


def test_a_named_file_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_project_config(tmp_path, tmp_path / "missing.yaml")


def test_values_are_read_and_severity_is_case_insensitive(tmp_path):
    write(tmp_path, "fail_on: medium\ntimeout: 900\nstrict: true\nmax_retries: 0\n")
    config = load_project_config(tmp_path)
    assert config.fail_on is Severity.MEDIUM
    assert config.timeout == 900
    assert config.strict is True
    assert config.max_retries == 0


def test_paths_are_relative_to_the_workspace_not_the_caller(tmp_path):
    write(tmp_path, "baseline: .plimsoll/baseline.json\npolicy_dir: rules\n")
    config = load_project_config(tmp_path)
    assert config.baseline == tmp_path / ".plimsoll" / "baseline.json"
    assert config.policy_dir == tmp_path / "rules"


def test_an_unknown_key_is_an_error_not_a_silent_default(tmp_path):
    # The typo that would otherwise leave the gate at HIGH while everyone believes LOW.
    write(tmp_path, "fail-on: low\n")
    with pytest.raises(ConfigError, match="fail-on"):
        load_project_config(tmp_path)


@pytest.mark.parametrize(
    "body",
    ["timeout: 0\n", "fail_on: severe\n", "- a list\n", "version: '2'\n", "timeout: [\n"],
)
def test_an_invalid_file_is_an_error(tmp_path, body):
    write(tmp_path, body)
    with pytest.raises(ConfigError):
        load_project_config(tmp_path)
