from plimsoll.core.redaction import Redactor, contains_secret, redact

PROVIDER_BLOCK = """
provider "aws" {
  region     = "us-east-1"
  access_key = "AKIAIOSFODNN7EXAMPLE"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}
"""


def test_provider_credentials_are_redacted():
    out = redact(PROVIDER_BLOCK)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "wJalrXUtnFEMI" not in out
    # The attribute names survive: the violation is that they are set inline at all.
    assert "access_key" in out
    assert "secret_key" in out
    assert "us-east-1" in out


def test_ordinary_terraform_is_untouched():
    hcl = 'resource "aws_s3_bucket" "reports" {\n  bucket = "acme"\n  acl = "public-read"\n}'
    assert redact(hcl) == hcl
    assert contains_secret(hcl) is False


def test_bare_tokens_without_a_keyword():
    assert "ghp_" not in redact("# leftover: ghp_16CharsMinimumAAAAAAAAAA")
    assert "AKIA" not in redact("AKIAIOSFODNN7EXAMPLE")
    assert "eyJ" not in redact("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4")


def test_private_key_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----"
    assert "MIIEow" not in redact(pem)


def test_redactor_round_trips():
    """The reversibility the prompt path depends on.

    Without it, applying a patch would write `[REDACTED]` over a live credential.
    """
    r = Redactor()
    hidden = r.redact(PROVIDER_BLOCK)
    assert "AKIAIOSFODNN7EXAMPLE" not in hidden
    assert r.restore(hidden) == PROVIDER_BLOCK


def test_distinct_secrets_get_distinct_placeholders():
    r = Redactor()
    text = 'a_token = "AAAAAAAAAAAAAAAA"\nb_token = "BBBBBBBBBBBBBBBB"'
    hidden = r.redact(text)
    assert "[REDACTED:1]" in hidden
    assert "[REDACTED:2]" in hidden
    assert r.restore(hidden) == text


def test_repeated_secret_reuses_one_placeholder():
    r = Redactor()
    hidden = r.redact('x_token = "SAMESAMESAMESAME"\ny_token = "SAMESAMESAMESAME"')
    assert r.count == 1
    assert hidden.count("[REDACTED:1]") == 2


def test_invented_placeholder_is_left_alone():
    """A hallucinated placeholder must not resolve to some other secret."""
    r = Redactor()
    r.redact('token = "AAAAAAAAAAAAAAAA"')
    assert r.restore("value = [REDACTED:9]") == "value = [REDACTED:9]"
