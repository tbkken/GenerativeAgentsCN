"""运行时回归测试：覆盖 ``test_secret_protection`` 对应的行为、故障边界和回归约束。"""
from cryptography.fernet import Fernet

from generative_agents.security import MasterKeyStore, SecretCipher


def test_master_key_file_is_created_once_and_ciphertext_is_version_safe(tmp_path):
    """回归验证 ``test_master_key_file_is_created_once_and_ciphertext_is_version_safe`` 所描述的业务结果、故障边界和隔离约束。"""
    store = MasterKeyStore(tmp_path)
    first_key = store.load_or_create()
    second_key = store.load_or_create()
    cipher = SecretCipher(first_key)

    first = cipher.encrypt("sk-private-value")
    second = cipher.encrypt("sk-private-value")

    assert first_key == second_key
    assert first.encrypted_value != second.encrypted_value
    assert first.fingerprint == second.fingerprint
    assert b"sk-private-value" not in first.encrypted_value
    assert cipher.decrypt(first.encrypted_value) == "sk-private-value"


def test_secret_rewrap_preserves_plaintext_without_reusing_ciphertext():
    """回归验证 ``test_secret_rewrap_preserves_plaintext_without_reusing_ciphertext`` 所描述的业务结果、故障边界和隔离约束。"""
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()
    encrypted = SecretCipher(old_key).encrypt("token-value").encrypted_value

    rewrapped = SecretCipher.rewrap(encrypted, old_key=old_key, new_key=new_key)

    assert rewrapped != encrypted
    assert SecretCipher(new_key).decrypt(rewrapped) == "token-value"
