"""Shared credential format preserves existing Sentry Fernet ciphertext."""
import json
from cryptography.fernet import Fernet, InvalidToken
from . import config
from .providers import ProviderError


def cipher():
    try:
        return Fernet(config.CONNECTION_KEY.encode())
    except (ValueError, TypeError):
        raise ProviderError('Connection encryption is not configured.') from None


def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, AttributeError):
        raise ProviderError('Saved credentials cannot be decrypted. Restore the encryption key or reconnect.') from None
