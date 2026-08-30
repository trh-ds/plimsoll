import pytest

from plimsoll.checks.addresses import split_address


@pytest.mark.parametrize(
    ("address", "expected_type"),
    [
        ("aws_s3_bucket.reports", "aws_s3_bucket"),
        ("data.aws_ami.latest", "aws_ami"),
        # A module path is stripped, so the type is the resource type, not "module".
        ("module.reports.aws_s3_bucket.data", "aws_s3_bucket"),
        ("module.a.module.b.aws_iam_role.r", "aws_iam_role"),
        ("module.reports.data.aws_ami.latest", "aws_ami"),
        ("", ""),
        ("bare", ""),
    ],
)
def test_split_address(address, expected_type):
    assert split_address(address) == (address, expected_type)
