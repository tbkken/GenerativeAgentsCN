"""运行时回归测试：覆盖 ``test_asset_store`` 对应的行为、故障边界和回归约束。"""
from io import BytesIO

import pytest

from generative_agents.assets import AssetStore, AssetValidationError


def test_same_asset_is_materialized_once_with_server_hash(tmp_path):
    """回归验证 ``test_same_asset_is_materialized_once_with_server_hash`` 所描述的业务结果、故障边界和隔离约束。"""
    store = AssetStore(tmp_path)
    payload = b'{"world":"ville","tiles":[]}'

    first = store.put_stream(
        BytesIO(payload),
        logical_name="../maze.json",
        declared_media_type="application/json",
    )
    second = store.put_stream(
        BytesIO(payload),
        logical_name="copy.json",
        declared_media_type="application/json; charset=utf-8",
    )

    assert first.created is True
    assert second.created is False
    assert first.sha256 == second.sha256
    assert first.logical_name == "maze.json"
    assert first.absolute_path == second.absolute_path
    assert store.resolve(first.relative_path, expected_sha256=first.sha256) == first.absolute_path


def test_asset_store_enforces_size_type_and_path_containment(tmp_path):
    """回归验证 ``test_asset_store_enforces_size_type_and_path_containment`` 所描述的业务结果、故障边界和隔离约束。"""
    store = AssetStore(tmp_path, max_bytes=8)
    with pytest.raises(AssetValidationError, match="maximum size"):
        store.put_stream(BytesIO(b"123456789"), logical_name="large.json")
    store = AssetStore(tmp_path, max_bytes=1024)
    with pytest.raises(AssetValidationError, match="does not match"):
        store.put_stream(
            BytesIO(b'{"ok":true}'),
            logical_name="fake.png",
            declared_media_type="image/png",
        )
    with pytest.raises(AssetValidationError, match="controlled relative"):
        store.resolve("../master.key")
