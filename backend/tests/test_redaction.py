import pytest

from app.collectors.redaction import MASK, Redactor

SECRET = "s3cr3t-Value_123"


@pytest.mark.parametrize(
    "text",
    [
        f"password={SECRET}",
        f"PASSWORD: {SECRET}",
        f'"password": "{SECRET}"',
        f"db_password={SECRET}&next=1",
        f"api_key={SECRET}",
        f"API-KEY: {SECRET}",
        f"access_token={SECRET}",
        f"client_secret={SECRET}",
        f"Authorization: Bearer {SECRET}",
        f"authorization: basic {SECRET}",
        f"Bearer {SECRET}",
        f"postgres://admin:{SECRET}@db:5432/app",
    ],
)
def test_common_secret_forms_are_removed(text):
    redactor = Redactor()

    result = redactor.text(text)

    assert SECRET not in result
    assert MASK in result
    assert redactor.count >= 1


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_abcdefghijklmnopqrstuvwxyz",
        "sk-or-v1-abcdefghijklmnopqrstuvwxyz",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcDEF123",
    ],
)
def test_recognisable_tokens_are_removed(secret):
    assert secret not in Redactor().text(f"value in the middle {secret} of a line")


def test_private_key_block_is_removed():
    block = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----"

    result = Redactor().text(f"before\n{block}\nafter")

    assert "abc" not in result
    assert "before" in result
    assert "after" in result


def test_unterminated_private_key_is_removed():
    assert "abc" not in Redactor().text("-----BEGIN PRIVATE KEY-----\nabc")


@pytest.mark.parametrize(
    "text",
    [
        "Back-off restarting failed container",
        "0/1 nodes are available: 1 Insufficient cpu.",
        'Failed to pull image "registry.invalid/app:1": not found',
        'secret "db-credentials" not found',
        "connection refused to http://db:5432/app",
        "exit code 137",
    ],
)
def test_ordinary_diagnostic_text_is_unchanged(text):
    redactor = Redactor()

    assert redactor.text(text) == text
    assert redactor.count == 0


def test_none_stays_none():
    assert Redactor().text(None) is None


def test_count_accumulates_across_calls():
    redactor = Redactor()

    redactor.text(f"password={SECRET}")
    redactor.text(f"token={SECRET}")

    assert redactor.count == 2
