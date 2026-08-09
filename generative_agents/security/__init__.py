"""Credential protection helpers; this is isolation, not user authorization."""

from .secrets import EncryptedSecret, MasterKeyStore, SecretCipher

__all__ = ["EncryptedSecret", "MasterKeyStore", "SecretCipher"]
