"""
Telegram initData verification.

This is the whole of Mini App identity: if `verify_init_data` can be
fooled, anyone can act as anyone. The expected hash here is computed
independently from Telegram's published algorithm rather than by calling
the implementation, so these tests check the implementation against the
specification instead of against itself.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.api.auth import INIT_DATA_MAX_AGE_SECONDS, verify_init_data

BOT_TOKEN = "123456:TEST-TOKEN-NOT-REAL"


def sign(fields: dict[str, str], token: str = BOT_TOKEN) -> str:
    """Builds a signed initData string exactly as Telegram documents it."""
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


@pytest.fixture
def valid_fields() -> dict[str, str]:
    return {
        "auth_date": str(int(time.time())),
        "query_id": "AAterEQAAAAAA",
        "user": json.dumps({"id": 5573610231, "first_name": "Test", "username": "tester"}),
    }


def test_valid_init_data_is_accepted(valid_fields):
    parsed = verify_init_data(sign(valid_fields), BOT_TOKEN)
    assert json.loads(parsed["user"])["id"] == 5573610231


def test_hash_is_stripped_from_returned_fields(valid_fields):
    assert "hash" not in verify_init_data(sign(valid_fields), BOT_TOKEN)


def test_tampered_user_id_is_rejected(valid_fields):
    """The impersonation attempt: keep a real signature, swap the user."""
    signed = sign(valid_fields)
    forged = signed.replace("5573610231", "9999999999")
    assert forged != signed
    with pytest.raises(ValueError):
        verify_init_data(forged, BOT_TOKEN)


def test_wrong_bot_token_is_rejected(valid_fields):
    with pytest.raises(ValueError):
        verify_init_data(sign(valid_fields, token="999:OTHER-TOKEN"), BOT_TOKEN)


def test_missing_hash_is_rejected(valid_fields):
    with pytest.raises(ValueError, match="hash"):
        verify_init_data(urlencode(valid_fields), BOT_TOKEN)


def test_empty_init_data_is_rejected():
    with pytest.raises(ValueError):
        verify_init_data("", BOT_TOKEN)


def test_expired_init_data_is_rejected(valid_fields):
    valid_fields["auth_date"] = str(int(time.time()) - INIT_DATA_MAX_AGE_SECONDS - 60)
    with pytest.raises(ValueError, match="expired"):
        verify_init_data(sign(valid_fields), BOT_TOKEN)


def test_init_data_just_inside_the_window_is_accepted(valid_fields):
    valid_fields["auth_date"] = str(int(time.time()) - INIT_DATA_MAX_AGE_SECONDS + 60)
    assert verify_init_data(sign(valid_fields), BOT_TOKEN)


def test_extra_fields_are_covered_by_the_signature(valid_fields):
    """
    Every field participates in the check string, so an attacker cannot
    append one. Guards against a future refactor that filters the fields
    it verifies down to a known subset.
    """
    signed = sign(valid_fields)
    with pytest.raises(ValueError):
        verify_init_data(signed + "&is_premium=true", BOT_TOKEN)
