"""Windows 已加载原生依赖更新探针的纯逻辑测试。"""

import pytest

from scripts.probe_windows_native_dependency_update import (
    TARGET_BUILD,
    _write_manifest,
    classify_online_attempt,
)


def _attempt(
        *,
        success: bool,
        fresh: int | None,
        loaded: int | None,
        fresh_version: str | None = "2.0.0",
        fresh_hash: str | None = "v2-hash",
) -> dict:
    """构造分类器所需的最小探针结果。"""
    return {
        "before": {"binary_sha256": "v1-hash"},
        "install_success": success,
        "fresh_process": {
            "distribution_version": fresh_version,
            "compiled_version": fresh,
            "binary_sha256": fresh_hash,
        },
        "loaded_after": {"compiled_version": loaded},
    }


def test_classify_online_attempt_distinguishes_failed_update_states():
    """安装失败后必须区分原载荷未变和环境已发生变化。"""
    assert classify_online_attempt(
        _attempt(
            success=False,
            fresh=100,
            loaded=100,
            fresh_version="1.0.0",
            fresh_hash="v1-hash",
        )
    ) == "install_blocked_unchanged"
    assert classify_online_attempt(
        _attempt(success=False, fresh=200, loaded=100)
    ) == "install_failed_with_environment_change"
    assert classify_online_attempt({
        **_attempt(success=False, fresh=None, loaded=100),
        "fresh_process": {"error": "ImportError: broken payload"},
    }) == "install_failed_with_environment_change"


def test_classify_online_attempt_distinguishes_restart_boundaries():
    """成功回执还要结合磁盘载荷和当前进程状态判定。"""
    assert classify_online_attempt(
        _attempt(
            success=True,
            fresh=100,
            loaded=100,
            fresh_version="1.0.0",
            fresh_hash="v1-hash",
        )
    ) == "reported_success_without_new_payload"
    assert classify_online_attempt(
        _attempt(success=True, fresh=TARGET_BUILD, loaded=100)
    ) == "restart_required_for_activation"
    assert classify_online_attempt(
        _attempt(success=True, fresh=TARGET_BUILD, loaded=TARGET_BUILD)
    ) == "online_activation_succeeded"


def test_classify_online_attempt_preserves_probe_failures():
    """探针自身异常不能被误报成系统文件锁或安装结果。"""
    attempt = _attempt(success=False, fresh=None, loaded=100)
    attempt["probe_error"] = "RuntimeError: fixture failed"
    assert classify_online_attempt(attempt) == "probe_error"


def test_write_manifest_covers_legacy_and_modern_plugin_contracts(tmp_path):
    """探针清单必须分别复现 requirements 与 PEP 621 入口。"""
    legacy = _write_manifest(
        tmp_path,
        kind="requirements",
        distribution_version="2.0.0",
    )
    modern = _write_manifest(
        tmp_path,
        kind="pyproject",
        distribution_version="2.0.0",
    )

    assert legacy.name == "requirements.txt"
    assert "moviepilot-native-update-probe==2.0.0" in legacy.read_text(
        encoding="utf-8"
    )
    assert modern.name == "pyproject.toml"
    assert 'dependencies = ["moviepilot-native-update-probe==2.0.0"]' in (
        modern.read_text(encoding="utf-8")
    )


def test_write_manifest_rejects_unknown_contract(tmp_path):
    """未知清单形态不能静默退化成 legacy 行为。"""
    with pytest.raises(ValueError, match="不支持的清单类型"):
        _write_manifest(
            tmp_path,
            kind="unknown",
            distribution_version="2.0.0",
        )
