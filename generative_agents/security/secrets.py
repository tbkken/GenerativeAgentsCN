"""Versioned secret encryption without exposing plaintext to definitions or logs."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    encrypted_value: bytes
    fingerprint: str


class MasterKeyStore:
    """Load a master key from the environment or a private local file."""

    ENVIRONMENT_KEY = "GA_MASTER_KEY"

    def __init__(self, var_dir: str | Path):
        self.path = Path(var_dir).resolve() / "master.key"

    def load_or_create(self) -> bytes:
        environment_value = os.environ.get(self.ENVIRONMENT_KEY)
        if environment_value:
            key = environment_value.strip().encode("ascii")
            self._validate(key)
            return key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=5):
            if self.path.exists():
                key = self.path.read_bytes().strip()
                self._validate(key)
                return key
            key = Fernet.generate_key()
            temporary = self.path.parent / f".master-{uuid4()}.tmp"
            try:
                with temporary.open("xb") as file_handle:
                    file_handle.write(key + b"\n")
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, self.path)
                return key
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate(key: bytes) -> None:
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError("GA master key is not a valid Fernet key") from exc


class SecretCipher:
    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        if not plaintext:
            raise ValueError("secret plaintext must not be empty")
        value = plaintext.encode("utf-8")
        digest = hashlib.sha256(value).hexdigest()
        return EncryptedSecret(
            encrypted_value=self._fernet.encrypt(value),
            fingerprint=f"sha256:{digest[:9]}",
        )

    def decrypt(self, encrypted_value: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted_value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("secret cannot be decrypted with the active master key") from exc

    @staticmethod
    def rewrap(
        encrypted_value: bytes,
        *,
        old_key: bytes,
        new_key: bytes,
    ) -> bytes:
        try:
            plaintext = Fernet(old_key).decrypt(encrypted_value)
        except InvalidToken as exc:
            raise ValueError("secret cannot be decrypted with the old master key") from exc
        return Fernet(new_key).encrypt(plaintext)
