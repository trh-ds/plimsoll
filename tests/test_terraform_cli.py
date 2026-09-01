"""The shared terraform plumbing: credential detection and the provider override.

The override file is the reason a scan works on a laptop with no AWS account. It is also
a file this tool writes into a copy of somebody's configuration, so the conditions under
which it appears are worth pinning down exactly.
"""

from __future__ import annotations

import subprocess

import pytest

from plimsoll.checks import terraform_cli
from plimsoll.checks.terraform_cli import (
    INIT_ATTEMPTS,
    OVERRIDE_FILENAME,
    base_env,
    has_default_aws_provider,
    has_real_credentials,
    is_transient_network_failure,
    needs_credential_override,
    parse_version,
    plugin_cache_dir,
    with_dummy_credentials,
)
from plimsoll.core.deadline import Deadline
from plimsoll.core.gate import CheckExecutionError
from plimsoll.core.models import CheckConfig

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


class FakeInit:
    """Stands in for `_run`, returning a scripted result per attempt."""

    def __init__(self, *results: tuple[int, str]) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self, args, cwd, env, timeout):
        self.calls += 1
        code, stderr = self.results[min(self.calls - 1, len(self.results) - 1)]
        return subprocess.CompletedProcess(args, code, stdout="", stderr=stderr)


@pytest.fixture
def no_sleeping(monkeypatch):
    """The backoff is real time, and a unit suite should not spend it."""
    slept: list[float] = []
    monkeypatch.setattr(terraform_cli.time, "sleep", slept.append)
    return slept


def init(monkeypatch, tmp_path, fake, deadline=None):
    monkeypatch.setattr(terraform_cli, "_run", fake)
    ctx = terraform_cli.CheckContext(
        tmp_path, CheckConfig(timeout_seconds=5), deadline=deadline or Deadline.none()
    )
    terraform_cli._init_with_retries(tmp_path, ctx)


_DIAL = "Error: Failed to install provider\n\ndial tcp 1.2.3.4:443: i/o timeout"
_BAD_CONFIG = 'Error: Invalid provider requirement\n\non main.tf line 3: version "~> not-a-version"'

# Captured verbatim from `uv run plimsoll scan tests/fixtures/compliant_s3` on Windows,
# terraform 1.15.8, on a connection whose IPv6 route to releases.hashicorp.com times out.
# Kept exactly as terraform emitted it, hard wrapping included: the first version of the
# pattern was written with literal spaces and did not match this, because terraform had
# put a newline between "read" and "tcp". The one real failure it existed for was the one
# it missed.
_REAL_WINDOWS_TIMEOUT = """\
Error: Failed to install provider

Error while installing hashicorp/aws v5.100.0: releases.hashicorp.com: read
tcp
[2401:4900:ae2d:ed84:60:3ac8:6570:87d5]:58540->[2600:9000:264a:da00:5:e2b6:b380:93a1]:443:
wsarecv: A connection attempt failed because the connected party did not
properly respond after a period of time, or established connection failed
because connected host has failed to respond.
"""


@pytest.mark.parametrize(
    "output",
    [
        _DIAL,
        _REAL_WINDOWS_TIMEOUT,
        "read: connection reset by peer",
        "dial tcp: lookup registry.terraform.io: no such host",
        "TLS handshake timeout",
        "unexpected EOF",
        "received 503 Service Unavailable",
        "Error: Failed to query available provider packages",
    ],
)
def test_transport_failures_are_recognised(output):
    assert is_transient_network_failure(output)


@pytest.mark.parametrize(
    "output",
    [
        _BAD_CONFIG,
        'Error: Unsupported argument\n\nAn argument named "buckt" is not expected here.',
        "Error: Module not installed",
        "",
    ],
)
def test_a_configuration_error_is_not_mistaken_for_the_network(output):
    assert is_transient_network_failure(output) is False


def test_a_registry_blip_is_retried_and_succeeds(monkeypatch, tmp_path, no_sleeping):
    fake = FakeInit((1, _DIAL), (0, ""))
    init(monkeypatch, tmp_path, fake)

    assert fake.calls == 2
    assert no_sleeping == [terraform_cli.INIT_BACKOFF_SECONDS[0]]


def test_a_persistent_network_failure_gives_up_and_says_it_was_the_network(
    monkeypatch, tmp_path, no_sleeping
):
    fake = FakeInit((1, _DIAL))
    with pytest.raises(CheckExecutionError) as exc:
        init(monkeypatch, tmp_path, fake)

    assert fake.calls == INIT_ATTEMPTS
    assert "network failure, not a problem with your configuration" in str(exc.value)


def test_a_configuration_error_is_not_retried(monkeypatch, tmp_path, no_sleeping):
    """Retrying it burns the run budget three times over to print the same message."""
    fake = FakeInit((1, _BAD_CONFIG))
    with pytest.raises(CheckExecutionError) as exc:
        init(monkeypatch, tmp_path, fake)

    assert fake.calls == 1
    assert no_sleeping == []
    assert "Invalid provider requirement" in str(exc.value)


def test_the_backoff_does_not_sleep_past_the_run_budget(monkeypatch, tmp_path, no_sleeping):
    """Waiting to retry something the run will not have time to use is a slower failure."""
    fake = FakeInit((1, _DIAL))
    with pytest.raises(CheckExecutionError):
        init(monkeypatch, tmp_path, fake, deadline=Deadline(1))

    assert fake.calls == 1
    assert no_sleeping == []


def test_a_first_attempt_success_does_not_retry(monkeypatch, tmp_path, no_sleeping):
    fake = FakeInit((0, ""))
    init(monkeypatch, tmp_path, fake)
    assert fake.calls == 1


def test_an_init_timeout_names_the_flag_that_fixes_it():
    """ "exceeded 120s" alone sends people to look at their Terraform, which is fine."""
    message = terraform_cli._timeout_message("init", 120)
    assert "--check-timeout" in message
    assert "TF_PLUGIN_CACHE_DIR" in message
    # Other subcommands do not reach the network, so the advice would be wrong there.
    assert terraform_cli._timeout_message("plan", 120) == "terraform plan exceeded 120s"


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
