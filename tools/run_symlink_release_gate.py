"""Strict release gate for native file and directory symlink boundaries.

Ordinary developer test runs may skip when the host cannot create native
symlinks.  This command is deliberately stricter: capability, collection,
execution and zero-skip evidence are all required for a release pass.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROL_TEST = "tests/architecture/test_run_observability_lifecycle_redlines.py"
SYMLINK_TEST_NODES = (
    f"{ROL_TEST}::test_def_047_log_service_rejects_a_real_symlink_chain",
    f"{ROL_TEST}::test_def_061_artifact_preview_and_download_enforce_persisted_storage_integrity[final_symlink]",
    f"{ROL_TEST}::test_def_061_artifact_preview_and_download_enforce_persisted_storage_integrity[intermediate_symlink]",
    f"{ROL_TEST}::test_def_061_artifact_cross_run_native_directory_symlink_is_rejected",
    f"{ROL_TEST}::test_def_063_replay_frame_integrity_blocks_manifest_window_and_artifact[final_symlink]",
    f"{ROL_TEST}::test_def_063_replay_frame_integrity_blocks_manifest_window_and_artifact[intermediate_symlink]",
    f"{ROL_TEST}::test_def_063_replay_cross_run_native_file_symlink_blocks_all_consumers",
)


class SymlinkCapabilityError(RuntimeError):
    """当前平台无法创建发布门禁要求的真实文件或目录符号链接。"""

    pass


def verify_native_symlink_capability() -> None:
    """Create and dereference a real file link and directory link."""

    try:
        with tempfile.TemporaryDirectory(prefix="ga-native-symlink-") as temporary:
            root = Path(temporary)
            file_target = root / "file-target.txt"
            file_target.write_text("native-file-link", encoding="utf-8")
            file_link = root / "file-link.txt"
            file_link.symlink_to(file_target)

            directory_target = root / "directory-target"
            directory_target.mkdir()
            (directory_target / "proof.txt").write_text(
                "native-directory-link", encoding="utf-8"
            )
            directory_link = root / "directory-link"
            directory_link.symlink_to(directory_target, target_is_directory=True)

            if not file_link.is_symlink() or file_link.read_text(encoding="utf-8") != "native-file-link":
                raise SymlinkCapabilityError("file symlink was not created or dereferenced")
            if not directory_link.is_symlink() or (
                directory_link / "proof.txt"
            ).read_text(encoding="utf-8") != "native-directory-link":
                raise SymlinkCapabilityError(
                    "directory symlink was not created or dereferenced"
                )
    except (OSError, NotImplementedError) as exc:
        hint = (
            "Enable Windows Developer Mode or grant SeCreateSymbolicLinkPrivilege"
            if os.name == "nt"
            else "Run on a filesystem and account that permit symbolic links"
        )
        raise SymlinkCapabilityError(f"{hint}: {exc}") from exc


def junit_counts(path: Path) -> tuple[int, int, int, int]:
    """从 pytest JUnit XML 中汇总总数、失败、错误和跳过数量。"""

    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return tuple(
        sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    )


def run_gate() -> int:
    """运行原生符号链接隔离测试，并验证测试数量和结果满足发布门禁。"""

    with tempfile.TemporaryDirectory(prefix="ga-symlink-junit-") as temporary:
        report = Path(temporary) / "native-symlink.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            *SYMLINK_TEST_NODES,
            "-q",
            "-rA",
            f"--junitxml={report}",
        ]
        environment = os.environ.copy()
        environment["GA_REQUIRE_NATIVE_SYMLINK_TESTS"] = "1"
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, check=False
        )
        if completed.returncode != 0:
            print(
                f"SYMLINK_RELEASE_GATE_FAILED pytest_exit={completed.returncode}",
                file=sys.stderr,
            )
            return completed.returncode or 1
        if not report.is_file():
            print("SYMLINK_RELEASE_GATE_FAILED junit_report_missing", file=sys.stderr)
            return 1
        tests, failures, errors, skipped = junit_counts(report)
        expected = len(SYMLINK_TEST_NODES)
        if (tests, failures, errors, skipped) != (expected, 0, 0, 0):
            print(
                "SYMLINK_RELEASE_GATE_FAILED "
                f"expected={expected} tests={tests} failures={failures} "
                f"errors={errors} skipped={skipped}",
                file=sys.stderr,
            )
            return 1
        print(
            "SYMLINK_RELEASE_GATE_PASSED "
            f"platform={sys.platform} tests={tests} file_symlink=1 directory_symlink=1"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    """解析命令行并执行符号链接安全发布门禁。"""

    parser = argparse.ArgumentParser(
        description="Run the seven native-symlink storage isolation release tests."
    )
    parser.add_argument(
        "--allow-unavailable",
        action="store_true",
        help="Local-only: report an explicit SKIP and return success if native links cannot be created.",
    )
    args = parser.parse_args(argv)
    if args.allow_unavailable and os.environ.get("CI", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        print(
            "SYMLINK_RELEASE_GATE_FAILED allow-unavailable is forbidden in CI",
            file=sys.stderr,
        )
        return 2
    try:
        verify_native_symlink_capability()
    except SymlinkCapabilityError as exc:
        prefix = "SYMLINK_RELEASE_GATE_SKIPPED" if args.allow_unavailable else "SYMLINK_RELEASE_GATE_FAILED"
        print(f"{prefix} capability_unavailable: {exc}", file=sys.stderr)
        return 0 if args.allow_unavailable else 2
    return run_gate()


if __name__ == "__main__":
    raise SystemExit(main())
