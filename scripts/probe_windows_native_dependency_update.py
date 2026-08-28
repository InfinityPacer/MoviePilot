"""验证 Windows 已加载原生扩展的插件依赖在线升级行为。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROBE_DISTRIBUTION = "moviepilot-native-update-probe"
PROBE_MODULE = "_moviepilot_native_update_probe"
INITIAL_VERSION = "1.0.0"
TARGET_VERSION = "2.0.0"
INITIAL_BUILD = 100
TARGET_BUILD = 200
MANIFEST_KINDS = ("requirements", "pyproject")


def classify_online_attempt(attempt: dict[str, Any]) -> str:
    """按安装回执、磁盘载荷和当前进程状态判定在线更新边界。"""
    if attempt.get("probe_error"):
        return "probe_error"
    fresh_process = attempt.get("fresh_process", {})
    loaded_after = attempt.get("loaded_after", {})
    if not attempt["install_success"]:
        before = attempt.get("before", {})
        unchanged = (
            fresh_process.get("distribution_version") == INITIAL_VERSION
            and fresh_process.get("compiled_version") == INITIAL_BUILD
            and fresh_process.get("binary_sha256") == before.get("binary_sha256")
        )
        return (
            "install_blocked_unchanged"
            if unchanged
            else "install_failed_with_environment_change"
        )
    if (
            fresh_process.get("distribution_version") != TARGET_VERSION
            or fresh_process.get("compiled_version") != TARGET_BUILD
    ):
        return "reported_success_without_new_payload"
    if loaded_after.get("compiled_version") != TARGET_BUILD:
        return "restart_required_for_activation"
    return "online_activation_succeeded"


def _sha256(path: Path) -> str | None:
    """返回当前二进制摘要；安装失败删除文件时返回空值。"""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
        command: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """执行探针子进程并保留完整文本结果。"""
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    if check and result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "没有输出"
        raise RuntimeError(f"探针子进程失败（{result.returncode}）：{details}")
    return result


def _write_probe_source(
        root: Path,
        *,
        distribution_version: str,
        compiled_version: int,
) -> Path:
    """生成具有固定模块路径和可辨识构建号的最小 C 扩展源码。"""
    source_dir = root / f"source-{distribution_version}"
    source_dir.mkdir(parents=True)
    (source_dir / "pyproject.toml").write_text(
        f"""
[build-system]
requires = ["setuptools>=77", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{PROBE_DISTRIBUTION}"
version = "{distribution_version}"
requires-python = ">=3.14"
""".lstrip(),
        encoding="utf-8",
    )
    (source_dir / "setup.py").write_text(
        f"""
from setuptools import Extension, setup

setup(ext_modules=[Extension("{PROBE_MODULE}", ["probe.c"])])
""".lstrip(),
        encoding="utf-8",
    )
    (source_dir / "probe.c").write_text(
        f"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *compiled_version(PyObject *self, PyObject *args) {{
    return PyLong_FromLong({compiled_version});
}}

static PyMethodDef probe_methods[] = {{
    {{"compiled_version", compiled_version, METH_NOARGS, "Return the probe build."}},
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef probe_module = {{
    PyModuleDef_HEAD_INIT,
    "{PROBE_MODULE}",
    NULL,
    -1,
    probe_methods,
    NULL,
    NULL,
    NULL,
    NULL
}};

PyMODINIT_FUNC PyInit_{PROBE_MODULE}(void) {{
    return PyModule_Create(&probe_module);
}}
""".lstrip(),
        encoding="utf-8",
    )
    return source_dir


def _write_manifest(
        root: Path,
        *,
        kind: str,
        distribution_version: str,
) -> Path:
    """生成与插件 legacy/modern 清单形态一致的精确版本依赖。"""
    manifest_dir = root / f"{kind}-{distribution_version}"
    manifest_dir.mkdir(parents=True)
    requirement = f"{PROBE_DISTRIBUTION}=={distribution_version}"
    if kind == "requirements":
        manifest = manifest_dir / "requirements.txt"
        manifest.write_text(requirement + "\n", encoding="utf-8")
        return manifest
    if kind == "pyproject":
        manifest = manifest_dir / "pyproject.toml"
        manifest.write_text(
            f"""
[project]
name = "moviepilot-native-update-consumer"
version = "1.0.0"
requires-python = ">=3.14"
dependencies = ["{requirement}"]
""".lstrip(),
            encoding="utf-8",
        )
        return manifest
    raise ValueError(f"不支持的清单类型：{kind}")


def _sanitize_message(message: str, probe_root: Path) -> str:
    """移除 CI 临时目录绝对路径，保留 uv 的原始错误语义。"""
    return message.replace(str(probe_root), "<probe-root>").replace(
        str(Path(sys.executable).resolve()),
        "<python>",
    )


def _fresh_process_state() -> dict[str, Any]:
    """由独立解释器读取磁盘上的发行版和原生构建号。"""
    code = f"""
import hashlib
import importlib.metadata
import json
from pathlib import Path

try:
    import {PROBE_MODULE} as probe
    binary_path = Path(probe.__file__)
    result = {{
        "distribution_version": importlib.metadata.version("{PROBE_DISTRIBUTION}"),
        "compiled_version": probe.compiled_version(),
        "binary_sha256": hashlib.sha256(binary_path.read_bytes()).hexdigest(),
    }}
except Exception as error:
    result = {{"error": f"{{type(error).__name__}}: {{error}}"}}
print(json.dumps(result, sort_keys=True))
"""
    result = _run([sys.executable, "-c", code], check=False)
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            "error": f"fresh process exited {result.returncode}",
            "stderr": result.stderr.strip(),
        }


async def _install_manifest(
        manifest: Path,
        wheelhouse: Path,
) -> tuple[bool, str]:
    """通过生产插件依赖入口安装指定清单。"""
    from app.adapters.external.market import PluginHelper

    return await PluginHelper().async_install_packages_with_fallback(
        manifest,
        [wheelhouse],
    )


def _loaded_phase(
        *,
        manifest: Path,
        wheelhouse: Path,
        probe_root: Path,
        output: Path,
) -> None:
    """保持 v1 .pyd 已加载时调用生产入口升级到 v2。"""
    module = importlib.import_module(PROBE_MODULE)
    module_path = Path(module.__file__).resolve()
    before = {
        "distribution_version": importlib.metadata.version(PROBE_DISTRIBUTION),
        "compiled_version": module.compiled_version(),
        "binary_sha256": _sha256(module_path),
    }
    attempt: dict[str, Any] = {
        "before": before,
        "install_success": False,
        "install_message": "",
    }
    try:
        install_success, install_message = asyncio.run(
            _install_manifest(manifest, wheelhouse)
        )
        attempt.update({
            "install_success": install_success,
            "install_message": _sanitize_message(install_message, probe_root),
            "loaded_after": {
                "compiled_version": module.compiled_version(),
                "binary_sha256": _sha256(module_path),
            },
            "fresh_process": _fresh_process_state(),
        })
    except Exception as error:
        attempt.update({
            "probe_error": f"{type(error).__name__}: {error}",
            "loaded_after": {
                "compiled_version": module.compiled_version(),
                "binary_sha256": _sha256(module_path),
            },
            "fresh_process": _fresh_process_state(),
        })
    finally:
        attempt["classification"] = classify_online_attempt(attempt)
        output.write_text(
            json.dumps(attempt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if attempt.get("probe_error"):
        raise RuntimeError(attempt["probe_error"])


def _loaded_phase_error(
        result: subprocess.CompletedProcess[str],
        probe_root: Path,
) -> str:
    """整理加载进程异常，供最终 JSON 保留诊断语义。"""
    details = result.stderr.strip() or result.stdout.strip() or "没有输出"
    return _sanitize_message(
        f"loaded phase exited {result.returncode}: {details}",
        probe_root,
    )


def _write_report(output: Path, report: dict[str, Any]) -> None:
    """增量写入 Windows 探针报告，避免失败证据随临时目录丢失。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_wheels(root: Path, uv: str) -> Path:
    """用当前 Python 构建同模块路径的 v1/v2 Windows wheel。"""
    wheelhouse = root / "wheels"
    wheelhouse.mkdir()
    for distribution_version, compiled_version in (
        (INITIAL_VERSION, INITIAL_BUILD),
        (TARGET_VERSION, TARGET_BUILD),
    ):
        source = _write_probe_source(
            root,
            distribution_version=distribution_version,
            compiled_version=compiled_version,
        )
        _run([
            uv,
            "build",
            "--wheel",
            "--python",
            sys.executable,
            "--out-dir",
            str(wheelhouse),
            str(source),
        ])
    return wheelhouse


def _uninstall_probe(uv: str) -> None:
    """在下一个清单场景前移除已退出进程使用的探针包。"""
    result = _run(
        [uv, "pip", "uninstall", "--python", sys.executable, PROBE_DISTRIBUTION],
        check=False,
    )
    remaining = _fresh_process_state()
    if "error" not in remaining:
        details = result.stderr.strip() or result.stdout.strip() or "没有输出"
        raise RuntimeError(f"探针包卸载后仍可导入：{remaining}; {details}")


def _run_manifest_scenario(
        *,
        kind: str,
        root: Path,
        wheelhouse: Path,
) -> dict[str, Any]:
    """执行安装 v1、在线升级 v2、退出加载进程后恢复三阶段。"""
    initial_manifest = _write_manifest(
        root,
        kind=kind,
        distribution_version=INITIAL_VERSION,
    )
    target_manifest = _write_manifest(
        root,
        kind=kind,
        distribution_version=TARGET_VERSION,
    )
    initial_success, initial_message = asyncio.run(
        _install_manifest(initial_manifest, wheelhouse)
    )
    if not initial_success:
        raise RuntimeError(f"{kind} v1 安装失败：{initial_message}")
    initial_state = _fresh_process_state()
    if initial_state.get("compiled_version") != INITIAL_BUILD:
        raise RuntimeError(f"{kind} v1 原生构建校验失败：{initial_state}")

    attempt_path = root / f"{kind}-online-attempt.json"
    child_env = os.environ.copy()
    child_env["UV_NO_INDEX"] = "1"
    loaded_result = _run([
        sys.executable,
        "-m",
        "scripts.probe_windows_native_dependency_update",
        "--loaded-phase",
        "--manifest",
        str(target_manifest),
        "--wheelhouse",
        str(wheelhouse),
        "--probe-root",
        str(root),
        "--output",
        str(attempt_path),
    ], check=False, env=child_env)
    if attempt_path.is_file():
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    else:
        attempt = {
            "classification": "probe_error",
            "probe_error": _loaded_phase_error(loaded_result, root),
        }
    loaded_phase_error = (
        (
            f"loaded phase exited {loaded_result.returncode}: "
            f"{attempt.get('probe_error', '没有生成探针错误信息')}"
        )
        if loaded_result.returncode != 0
        else None
    )

    recovery_success, recovery_message = asyncio.run(
        _install_manifest(target_manifest, wheelhouse)
    )
    recovery_state = _fresh_process_state()
    recovery_complete = (
        recovery_success
        and recovery_state.get("distribution_version") == TARGET_VERSION
        and recovery_state.get("compiled_version") == TARGET_BUILD
    )
    return {
        "manifest_kind": kind,
        "initial": initial_state,
        "initial_message": _sanitize_message(initial_message, root),
        "online_attempt": attempt,
        "loaded_phase_error": loaded_phase_error,
        "after_loaded_process_exit": {
            "install_success": recovery_success,
            "install_message": _sanitize_message(recovery_message, root),
            "fresh_process": recovery_state,
        },
        "scenario_complete": loaded_phase_error is None and recovery_complete,
    }


def _orchestrate(output: Path) -> None:
    """在 Windows 临时目录中运行两种插件清单场景并保留 JSON 证据。"""
    if platform.system() != "Windows":
        raise RuntimeError("该探针必须在真实 Windows 环境运行")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("未找到 uv 可执行文件")
    report: dict[str, Any] = {
        "schema_version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "results": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="moviepilot-native-update-") as temp_dir:
            root = Path(temp_dir).resolve()
            wheelhouse = _build_wheels(root, uv)
            previous_no_index = os.environ.get("UV_NO_INDEX")
            os.environ["UV_NO_INDEX"] = "1"
            try:
                for kind in MANIFEST_KINDS:
                    _uninstall_probe(uv)
                    report["results"].append(
                        _run_manifest_scenario(
                            kind=kind,
                            root=root,
                            wheelhouse=wheelhouse,
                        )
                    )
                    _write_report(output, report)
                _uninstall_probe(uv)
            finally:
                if previous_no_index is None:
                    os.environ.pop("UV_NO_INDEX", None)
                else:
                    os.environ["UV_NO_INDEX"] = previous_no_index
    except Exception as error:
        report["fatal_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        _write_report(output, report)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    incomplete = [
        result["manifest_kind"]
        for result in report["results"]
        if not result["scenario_complete"]
    ]
    if incomplete:
        raise RuntimeError(f"Windows 原生依赖更新探针未完整执行：{', '.join(incomplete)}")


def main() -> None:
    """解析 CI 与内部加载阶段参数。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--loaded-phase", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--probe-root", type=Path)
    arguments = parser.parse_args()
    if arguments.loaded_phase:
        if not all((arguments.manifest, arguments.wheelhouse, arguments.probe_root)):
            parser.error("loaded phase requires manifest, wheelhouse and probe-root")
        _loaded_phase(
            manifest=arguments.manifest,
            wheelhouse=arguments.wheelhouse,
            probe_root=arguments.probe_root,
            output=arguments.output,
        )
        return
    _orchestrate(arguments.output)


if __name__ == "__main__":
    main()
