"""Shared random code generation — avoids duplicating this in every place a code is minted."""
import secrets
import string

_ALPHABET = string.ascii_uppercase + string.digits


def generate_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
