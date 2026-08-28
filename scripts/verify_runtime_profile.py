"""跨平台 CI 使用的运行依赖 profile 验证入口。"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

from app.doctor import dependencies as dependency_doctor
from app.runtime.dependencies import runtime_dependency_health_errors


def verify_uv_environment() -> None:
    """执行 uv 元数据健康检查，并应用项目声明的传递依赖排除策略。"""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("未找到 uv 可执行文件")
    result = subprocess.run(
        [uv, "pip", "check", "--python", sys.executable],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode == 0:
        return

    project_file = Path(os.environ.get("UV_PROJECT_FILE", "pyproject.toml")).resolve()
    errors = runtime_dependency_health_errors(
        "\n".join((result.stdout, result.stderr)),
        project_file,
        retain_unparsed=True,
    )
    if errors:
        raise RuntimeError("uv 依赖健康检查失败：" + " | ".join(sorted(errors)))


def verify_platform_profile(
        *,
        expected_profile: str,
        expected_system: str,
        expected_machine: str,
) -> None:
    """验证运行平台、解释器 ABI 和 Windows profile 能力边界。"""
    current_system = platform.system()
    current_machine = platform.machine()
    if current_system != expected_system:
        raise RuntimeError(f"运行系统不匹配：{current_system} != {expected_system}")
    if current_machine != expected_machine:
        raise RuntimeError(f"机器架构不匹配：{current_machine} != {expected_machine}")

    free_threaded = sysconfig.get_config_var("Py_GIL_DISABLED") == 1
    expected_free_threaded = expected_profile == "free-threaded"
    if free_threaded != expected_free_threaded:
        raise RuntimeError(
            f"解释器 profile 不匹配：free-threaded={free_threaded}"
        )

    import_module("docker")
    if find_spec("pympler") is not None:
        raise RuntimeError("运行环境仍包含已移除的 Pympler")
    if current_system == "Windows":
        docker_transport = import_module("docker.transport")
        has_pywin32 = find_spec("win32api") is not None
        if has_pywin32 != (not expected_free_threaded):
            raise RuntimeError(
                f"Windows profile 的 pywin32 状态不匹配：installed={has_pywin32}"
            )
        if expected_free_threaded:
            if hasattr(docker_transport, "NpipeHTTPAdapter"):
                raise RuntimeError("Windows free-threaded profile 意外启用了 Docker named-pipe 能力")
        else:
            import_module("win32api")
            if not hasattr(docker_transport, "NpipeHTTPAdapter"):
                raise RuntimeError("Windows standard profile 缺少 Docker named-pipe 能力")


def verify_application_lifecycle() -> None:
    """在隔离配置下运行 FastAPI lifespan 并验证 readiness。"""
    from app.testing.bootstrap import ensure_sites_stub, isolate_config_dir

    isolate_config_dir()
    ensure_sites_stub()
    from fastapi.testclient import TestClient

    from app.factory import create_app

    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
        if response.status_code != 200:
            raise RuntimeError(f"应用 readiness 验证失败：{response.text}")
        if sysconfig.get_config_var("Py_GIL_DISABLED") == 1 and sys._is_gil_enabled():
            raise RuntimeError("应用启动后启用了 GIL")


def main(
        *,
        expected_profile: str,
        expected_system: str,
        expected_machine: str,
) -> None:
    """验证锁定环境、原生能力和应用生命周期。"""
    dependency_doctor.main(full=True)
    verify_platform_profile(
        expected_profile=expected_profile,
        expected_system=expected_system,
        expected_machine=expected_machine,
    )
    verify_uv_environment()
    verify_application_lifecycle()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-profile",
        required=True,
        choices=("standard", "free-threaded"),
    )
    parser.add_argument("--expected-system", required=True)
    parser.add_argument("--expected-machine", required=True)
    arguments = parser.parse_args()
    main(
        expected_profile=arguments.expected_profile,
        expected_system=arguments.expected_system,
        expected_machine=arguments.expected_machine,
    )
