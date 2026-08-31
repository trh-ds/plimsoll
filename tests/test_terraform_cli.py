"""The shared terraform plumbing: credential detection and the provider override.

The override file is the reason a scan works on a laptop with no AWS account. It is also
a file this tool writes into a copy of somebody's configuration, so the conditions under
which it appears are worth pinning down exactly.
"""

from __future__ import annotations

import pytest

from plimsoll.checks import terraform_cli
from plimsoll.checks.terraform_cli import (
    OVERRIDE_FILENAME,
    base_env,
    has_default_aws_provider,
    has_real_credentials,
    needs_credential_override,
    parse_version,
    plugin_cache_dir,
    with_dummy_credentials,
)

_AWS_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)


@pytest.fixture
def no_aws(monkeypatch):
    for name in _AWS_ENV:
        monkeypatch.delenv(name, raising=False)


def write(tmp_path, body: str, name: str = "main.tf"):
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_a_default_provider_block_is_found(tmp_path):
    assert has_default_aws_provider(
        write(tmp_path, 'provider "aws" {\n  region = "us-east-1"\n}\n')
    )


def test_a_provider_block_with_no_body_is_found(tmp_path):
    assert has_default_aws_provider(write(tmp_path, 'provider "aws" {}\n'))


def test_an_aliased_only_provider_is_not_overridable(tmp_path):
    # Terraform's override files merge into an existing block and error when there is
    # none to merge into. An aliased-only setup gets the honest plan failure.
    body = 'provider "aws" {\n  alias  = "west"\n  region = "us-west-2"\n}\n'
    assert has_default_aws_provider(write(tmp_path, body)) is False


def test_a_default_block_alongside_an_aliased_one_is_still_overridable(tmp_path):
    body = (
        'provider "aws" {\n  alias  = "west"\n  region = "us-west-2"\n}\n\n'
        'provider "aws" {\n  region = "us-east-1"\n}\n'
    )
    assert has_default_aws_provider(write(tmp_path, body))


def test_a_provider_for_another_cloud_does_not_count(tmp_path):
    assert has_default_aws_provider(write(tmp_path, 'provider "google" {\n}\n')) is False


def test_no_terraform_files_means_no_provider(tmp_path):
    assert has_default_aws_provider(tmp_path) is False


def test_an_undecodable_file_does_not_stop_the_search(tmp_path):
    (tmp_path / "a.tf").write_bytes(b"\xff\xfe\x00binary")
    write(tmp_path, 'provider "aws" {\n}\n', "b.tf")
    assert has_default_aws_provider(tmp_path)


@pytest.mark.parametrize("name", _AWS_ENV)
def test_any_real_credential_path_counts(monkeypatch, no_aws, name):
    monkeypatch.setenv(name, "something")
    assert has_real_credentials()


def test_no_credentials_is_the_laptop_case(no_aws):
    assert has_real_credentials() is False


def test_the_override_is_written_only_when_both_conditions_hold(monkeypatch, no_aws, tmp_path):
    write(tmp_path, 'provider "aws" {\n}\n')
    assert needs_credential_override(tmp_path) is True

    # Real credentials: leave the caller's identity alone. A CI job under a read-only
    # role produces a more accurate plan than any placeholder.
    monkeypatch.setenv("AWS_PROFILE", "readonly")
    assert needs_credential_override(tmp_path) is False


def test_no_provider_block_means_no_override(no_aws, tmp_path):
    write(tmp_path, 'resource "aws_s3_bucket" "reports" {\n}\n')
    assert needs_credential_override(tmp_path) is False


def test_placeholders_never_overwrite_a_real_credential(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREALKEYFROMTHECI")
    env = with_dummy_credentials({"AWS_ACCESS_KEY_ID": "AKIAREALKEYFROMTHECI"})
    assert env["AWS_ACCESS_KEY_ID"] == "AKIAREALKEYFROMTHECI"
    # ...but the ones that were not set are filled in.
    assert env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"


def test_the_run_environment_is_non_interactive_and_caches_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(tmp_path / "cache"))
    env = base_env()

    assert env["TF_IN_AUTOMATION"] == "1"
    assert env["TF_INPUT"] == "0"
    assert env["CHECKPOINT_DISABLE"] == "1"
    assert env["TF_PLUGIN_CACHE_DIR"] == str(tmp_path / "cache")
    assert (tmp_path / "cache").is_dir()


def test_a_configured_plugin_cache_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", str(tmp_path / "mine"))
    assert plugin_cache_dir() == tmp_path / "mine"


def test_the_default_plugin_cache_is_per_user(monkeypatch, tmp_path):
    monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert plugin_cache_dir() == tmp_path / "plimsoll" / "terraform-plugins"


def test_parse_version_reads_the_json_output():
    assert parse_version('{"terraform_version": "1.15.8"}') == "1.15.8"


@pytest.mark.parametrize("stdout", ["not json", "[]", "{}", ""])
def test_an_unreadable_version_is_none(stdout):
    assert parse_version(stdout) is None


def test_apply_is_not_reachable_from_this_module():
    # Invariant 5, asserted rather than intended. This module runs version, init,
    # validate, plan, and show, and that list is exhaustive.
    source = terraform_cli.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert '"apply"' not in text
    assert "'apply'" not in text


def test_the_override_only_relaxes_checks_about_the_caller():
    # None of these change how any resource is planned, which is the only thing the rule
    # pack reads.
    override = terraform_cli.CREDENTIAL_OVERRIDE
    assert OVERRIDE_FILENAME.endswith(".tf")
    assert "skip_credentials_validation" in override
    assert "skip_requesting_account_id" in override
    assert "skip_region_validation" in override
    assert "access_key" not in override
    assert "secret_key" not in override
