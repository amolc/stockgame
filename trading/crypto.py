import base64
import hashlib
import os

from Crypto.Cipher import AES
from django.conf import settings

_PREFIX = "enc:v1:"


def _get_aes_key() -> bytes:
    raw = os.getenv("FIELD_ENCRYPTION_KEY") or settings.SECRET_KEY
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    """Encrypt an API secret with AES-256-GCM before storing in the database."""
    if not plaintext:
        return ""
    cipher = AES.new(_get_aes_key(), AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    blob = cipher.nonce + tag + ciphertext
    return _PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_secret(stored_value: str) -> str:
    """Decrypt an encrypted API secret; returns empty string if empty or invalid."""
    if not stored_value:
        return ""
    if not stored_value.startswith(_PREFIX):
        return stored_value
    try:
        blob = base64.urlsafe_b64decode(stored_value[len(_PREFIX):].encode("ascii"))
        nonce, tag, ciphertext = blob[:16], blob[16:32], blob[32:]
        cipher = AES.new(_get_aes_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except Exception:
        return ""
