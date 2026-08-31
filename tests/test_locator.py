"""Mapping a plan-JSON resource address back to the block that declared it.

Plan JSON has no filenames and no line numbers. Without this, an OPA finding cannot be
reported to a human or patched by the loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plimsoll.checks.locator import locate_resource, terraform_files

SOURCE = """terraform {
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "reports" {
  bucket = "acme-reports"

  tags = {
    team = "data"
  }
}

data "aws_caller_identity" "current" {}

module "network" {
  source = "./network"
}
"""


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "main.tf").write_text(SOURCE, encoding="utf-8")
    return tmp_path


def test_a_resource_address_finds_its_block(workspace):
    located = locate_resource(workspace, "aws_s3_bucket.reports")
    assert located.file == Path("main.tf")
    assert located.line_start == 11
    assert located.line_end == 17


def test_a_data_address_finds_its_block(workspace):
    located = locate_resource(workspace, "data.aws_caller_identity.current")
    assert located.line_start == 19
    # A block whose braces balance on the opening line ends where it starts.
    assert located.line_end == 19


def test_a_provider_address_finds_its_block(workspace):
    located = locate_resource(workspace, "provider.aws")
    assert located.line_start == 7


@pytest.mark.parametrize(
    "address",
    [
        "aws_s3_bucket.reports[0]",
        'aws_s3_bucket.reports["primary"]',
        "module.network.aws_s3_bucket.reports",
        "module.network.module.inner.aws_s3_bucket.reports",
        "module.network[0].aws_s3_bucket.reports",
    ],
)
def test_indexes_and_module_prefixes_are_stripped(workspace, address):
    # The index and the module path are not part of the declaration we are looking for.
    assert locate_resource(workspace, address).line_start == 11


def test_an_unlocatable_address_falls_back_to_the_first_file(workspace):
    # Deliberate: a rule like logging.cloudtrail.absent fires on something the config
    # does not contain, so there is no block to point at. The patch prompt still needs a
    # file to add the resource to.
    located = locate_resource(workspace, "aws_cloudtrail.audit")
    assert located.file == Path("main.tf")
    assert (located.line_start, located.line_end) == (0, 0)


def test_an_empty_address_falls_back_too(workspace):
    assert locate_resource(workspace, "").file == Path("main.tf")


def test_a_workspace_with_no_terraform_files_locates_nothing(tmp_path):
    located = locate_resource(tmp_path, "aws_s3_bucket.reports")
    assert located.file == Path("")
    assert (located.line_start, located.line_end) == (0, 0)


def test_the_fallback_file_is_stable_across_runs(tmp_path):
    # An address that cannot be located must still produce a stable `Finding.id`, or the
    # same violation looks like a different one on the next scan.
    for name in ("z.tf", "a.tf", "m.tf"):
        (tmp_path / name).write_text("# empty\n", encoding="utf-8")
    assert locate_resource(tmp_path, "aws_s3_bucket.x").file == Path("a.tf")


def test_the_search_spans_every_file_in_order(tmp_path):
    (tmp_path / "a.tf").write_text("# nothing here\n", encoding="utf-8")
    (tmp_path / "b.tf").write_text('resource "aws_sns_topic" "alerts" {\n}\n', encoding="utf-8")

    located = locate_resource(tmp_path, "aws_sns_topic.alerts")
    assert located.file == Path("b.tf")
    assert located.line_start == 1


def test_nested_directories_are_searched(tmp_path):
    nested = tmp_path / "modules" / "network"
    nested.mkdir(parents=True)
    (nested / "sg.tf").write_text('resource "aws_security_group" "web" {\n}\n', encoding="utf-8")

    located = locate_resource(tmp_path, "aws_security_group.web")
    assert located.file == Path("modules/network/sg.tf")


def test_terraform_files_is_sorted_and_files_only(tmp_path):
    (tmp_path / "b.tf").write_text("", encoding="utf-8")
    (tmp_path / "a.tf").write_text("", encoding="utf-8")
    (tmp_path / "notes.md").write_text("", encoding="utf-8")
    (tmp_path / "a.tf.d").mkdir()

    assert [p.name for p in terraform_files(tmp_path)] == ["a.tf", "b.tf"]


def test_an_undecodable_file_is_skipped_rather_than_fatal(tmp_path):
    (tmp_path / "a.tf").write_bytes(b"\xff\xfe\x00binary")
    (tmp_path / "b.tf").write_text('resource "aws_sns_topic" "alerts" {\n}\n', encoding="utf-8")

    assert locate_resource(tmp_path, "aws_sns_topic.alerts").file == Path("b.tf")
