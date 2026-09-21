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
    """可持久化的版本化密文、随机 nonce 和认证标签。"""

    encrypted_value: bytes
    fingerprint: str


class MasterKeyStore:
    """Load a master key from the environment or a private local file."""

    ENVIRONMENT_KEY = "GA_MASTER_KEY"

    def __init__(self, var_dir: str | Path):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self.path = Path(var_dir).resolve() / "master.key"

    def load_or_create(self) -> bytes:
        """加载`or``create`。

        返回:
            返回 `bytes` 类型的处理结果。
        """
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
        """执行`validate`的内部处理，供当前模块或类复用。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`bytes`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError("GA master key is not a valid Fernet key") from exc


class SecretCipher:
    """使用本地主密钥加密、解密和轮换业务凭据。"""

    def __init__(self, key: bytes):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`bytes`。

        返回:
            无返回值。
        """
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        """执行 `SecretCipher` 的`encrypt`操作。

        参数:
            plaintext: 需要加密或验证的原始明文。 类型：`str`。

        返回:
            返回 `EncryptedSecret` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if not plaintext:
            raise ValueError("secret plaintext must not be empty")
        value = plaintext.encode("utf-8")
        digest = hashlib.sha256(value).hexdigest()
        return EncryptedSecret(
            encrypted_value=self._fernet.encrypt(value),
            fingerprint=f"sha256:{digest[:9]}",
        )

    def decrypt(self, encrypted_value: bytes) -> str:
        """执行 `SecretCipher` 的`decrypt`操作。

        参数:
            encrypted_value: 包含版本、随机数和密文的加密结果。 类型：`bytes`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        try:
            return self._fernet.decrypt(encrypted_value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError(
                "secret cannot be decrypted with the active master key"
            ) from exc

    @staticmethod
    def rewrap(
        encrypted_value: bytes,
        *,
        old_key: bytes,
        new_key: bytes,
    ) -> bytes:
        """执行 `SecretCipher` 的`rewrap`操作。

        参数:
            encrypted_value: 包含版本、随机数和密文的加密结果。 类型：`bytes`。
            old_key: 用于稳定定位`old`的键。 类型：`bytes`。
            new_key: 用于稳定定位`new`的键。 类型：`bytes`。

        返回:
            返回 `bytes` 类型的处理结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        try:
            plaintext = Fernet(old_key).decrypt(encrypted_value)
        except InvalidToken as exc:
            raise ValueError(
                "secret cannot be decrypted with the old master key"
            ) from exc
        return Fernet(new_key).encrypt(plaintext)
