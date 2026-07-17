from cryptography.fernet import Fernet
from fastapi import Depends, Header, HTTPException, status

from teampulse.config import Settings, get_settings


class CredentialCipher:
    """Small encryption boundary; replace key custody with KMS in production."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode()


async def require_api_key(
    x_teampulse_api_key: str | None = Header(default=None, alias="X-TeamPulse-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.api_key is None:
        return
    expected = settings.api_key.get_secret_value()
    if not x_teampulse_api_key or x_teampulse_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
