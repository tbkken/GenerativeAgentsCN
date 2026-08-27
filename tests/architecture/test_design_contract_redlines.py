"""Executable consistency checks for gaps found between UX and API/state design."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN = (REPO_ROOT / "docs" / "experiment-web-service-technical-design.md").read_text(
    encoding="utf-8"
)
PROTOTYPE = (REPO_ROOT / "docs" / "experiment-console.html").read_text(encoding="utf-8")


def test_def_018_published_revision_can_be_run_again_without_new_draft() -> None:
    """回归验证 ``test_def_018_published_revision_can_be_run_again_without_new_draft`` 所描述的业务结果、故障边界和隔离约束。"""
    accepted_contracts = (
        "POST | `/experiments/{id}/revisions/{revision_id}/runs`",
        "POST | `/revisions/{revision_id}/runs`",
        "POST | `/experiments/{id}/actions/run-published-revision`",
    )
    assert any(contract in DESIGN for contract in accepted_contracts), (
        "DEF-018 no API exists to start another Run from an already published immutable revision"
    )


def test_def_020_paused_run_can_be_cancelled_without_resuming_a_worker() -> None:
    """回归验证 ``test_def_020_paused_run_can_be_cancelled_without_resuming_a_worker`` 所描述的业务结果、故障边界和隔离约束。"""
    assert "PAUSED --> CANCELLED" in DESIGN, (
        "DEF-020 state machine has no PAUSED -> CANCELLED transition"
    )
    cancel_row = next(line for line in DESIGN.splitlines() if "`/runs/{run_id}/cancel`" in line)
    assert "PAUSED" in cancel_row, "DEF-020 cancel API excludes PAUSED even though it is an open Run"


def test_def_021_terminal_failure_states_are_discoverable_from_status_tabs() -> None:
    """回归验证 ``test_def_021_terminal_failure_states_are_discoverable_from_status_tabs`` 所描述的业务结果、故障边界和隔离约束。"""
    status_tabs = PROTOTYPE[PROTOTYPE.index('aria-label="实验状态筛选"') : PROTOTYPE.index("</div>", PROTOTYPE.index('aria-label="实验状态筛选"'))]
    assert "失败" in status_tabs or "异常" in status_tabs, (
        "DEF-021 FAILED experiments have no status tab in the high-fidelity list interaction"
    )
    assert "取消" in status_tabs or "终止" in status_tabs or "异常" in status_tabs, (
        "DEF-021 CANCELLED experiments have no status tab in the high-fidelity list interaction"
    )


def test_def_022_result_run_selector_exposes_history_pagination() -> None:
    """回归验证 ``test_def_022_result_run_selector_exposes_history_pagination`` 所描述的业务结果、故障边界和隔离约束。"""
    selector_start = PROTOTYPE.index('id="resultRunSelect"')
    result_area = PROTOTYPE[selector_start : PROTOTYPE.index('class="result-tabs"', selector_start)]
    pagination_cues = ("加载更多", "查看全部运行", "搜索运行", "run-history")
    assert any(cue in result_area for cue in pagination_cues), (
        "DEF-022 result Run selector has no way to reach runs beyond the first cursor page"
    )


def test_def_023_world_assets_have_upload_and_content_delivery_apis() -> None:
    """回归验证 ``test_def_023_world_assets_have_upload_and_content_delivery_apis`` 所描述的业务结果、故障边界和隔离约束。"""
    upload_contracts = ("POST | `/assets`", "POST | `/assets/upload`")
    read_contracts = (
        "GET | `/assets/{asset_id}`",
        "GET | `/assets/{asset_id}/content`",
        "GET | `/assets/{asset_hash}`",
    )
    assert any(contract in DESIGN for contract in upload_contracts), (
        "DEF-023 world/portrait assets are content-addressed but no upload API is defined"
    )
    assert any(contract in DESIGN for contract in read_contracts), (
        "DEF-023 timeline/portrait asset IDs have no controlled content-delivery API"
    )
