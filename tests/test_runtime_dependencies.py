import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

from app.doctor import dependencies as dependency_doctor
from app.foundation import environment
from app.runtime import dependencies
from scripts import verify_runtime_profile


def test_free_threaded_runtime_tracks_interpreter_build(monkeypatch):
    monkeypatch.setattr(
        environment.sysconfig,
        "get_config_var",
        lambda name: 1 if name == "Py_GIL_DISABLED" else None,
    )

    assert environment.is_free_threaded_runtime() is True


def test_gil_status_tracks_current_interpreter_state(monkeypatch):
    monkeypatch.setattr(environment.sys, "_is_gil_enabled", lambda: False)

    assert environment.is_gil_enabled() is False


def test_runtime_dependency_group_tracks_interpreter_abi(monkeypatch):
    monkeypatch.setattr(dependencies, "is_free_threaded_runtime", lambda: False)
    assert dependencies.runtime_dependency_group() == "runtime-standard"

    monkeypatch.setattr(dependencies, "is_free_threaded_runtime", lambda: True)
    assert dependencies.runtime_dependency_group() == "runtime-free-threaded"


def test_runtime_requirements_include_project_and_active_group(tmp_path: Path, monkeypatch):
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        """
[project]
dependencies = ["shared==1"]

[dependency-groups]
runtime-standard = ["standard==2"]
runtime-free-threaded = ["free-threaded==3"]
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dependencies,
        "runtime_dependency_group",
        lambda: "runtime-free-threaded",
    )

    assert list(dependencies.iter_runtime_requirement_strings(project_file)) == [
        "shared==1",
        "free-threaded==3",
    ]
    assert list(dependencies.iter_runtime_profile_requirement_strings(project_file)) == [
        "free-threaded==3",
    ]


def test_runtime_profiles_share_gil_safe_crcmod_distribution():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as file:
        document = tomllib.load(file)

    assert "crcmod-plus==2.3.1" in document["project"]["dependencies"]
    groups = document["dependency-groups"]
    assert all(
        not requirement.lower().startswith("crcmod")
        for group in ("runtime-standard", "runtime-free-threaded")
        for requirement in groups[group]
    )
    assert {
        "package": {"name": "oss2"},
        "dependencies": ["crcmod"],
    } in document["tool"]["uv"]["exclude-dependencies"]


def test_windows_pywin32_is_limited_to_standard_runtime():
    """Windows free-threaded 环境不能继承仅提供标准 ABI 的 pywin32。"""
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as file:
        document = tomllib.load(file)

    project_dependencies = {
        Requirement(requirement).name.lower()
        for requirement in document["project"]["dependencies"]
    }
    assert "pympler" not in project_dependencies
    assert "pywin32" not in project_dependencies

    groups = document["dependency-groups"]
    standard_pywin32 = [
        Requirement(requirement)
        for requirement in groups["runtime-standard"]
        if Requirement(requirement).name.lower() == "pywin32"
    ]
    assert len(standard_pywin32) == 1
    assert str(standard_pywin32[0].marker) == 'sys_platform == "win32"'
    assert all(
        Requirement(requirement).name.lower() != "pywin32"
        for requirement in groups["runtime-free-threaded"]
    )
    assert {
        "package": {"name": "docker"},
        "dependencies": ["pywin32"],
    } in document["tool"]["uv"]["exclude-dependencies"]


def test_standard_runtime_uses_shared_rust_text_capabilities(monkeypatch):
    """标准镜像的文本能力不得重新引入 profile 专属实现。"""
    imported = []
    moviepilot_rust = SimpleNamespace(
        is_available=lambda: True,
        jieba_cut=lambda _value: ["中文", "分词"],
        zhconv_fast=lambda value, _target: value,
    )

    def import_module(name):
        imported.append(name)
        return moviepilot_rust if name == "moviepilot_rust" else SimpleNamespace()

    monkeypatch.setattr(dependency_doctor, "import_module", import_module)
    monkeypatch.setattr(dependency_doctor.sysconfig, "get_config_var", lambda _name: None)

    dependency_doctor.main()

    assert "zhconv_rs" not in imported


def test_runtime_excluded_dependency_pairs_reads_uv_policy(tmp_path: Path):
    """运行时诊断应复用 uv 排除配置，不维护第二份包名特判。"""
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        """
[tool.uv]
exclude-dependencies = [
    { package = { name = "Demo_Package" }, dependencies = ["Legacy-Dep>=1"] },
]
""",
        encoding="utf-8",
    )

    assert dependencies.runtime_excluded_dependency_pairs(project_file) == {
        ("demo-package", "legacy-dep")
    }


def test_runtime_dependency_health_errors_filters_declared_exclusions(tmp_path: Path):
    """环境健康检查只忽略 uv 策略中明确声明的依赖边。"""
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        """
[tool.uv]
exclude-dependencies = [
    { package = { name = "docker" }, dependencies = ["pywin32"] },
]
""",
        encoding="utf-8",
    )
    ignored = "The package `docker` requires `pywin32>=304`, but it's not installed"
    actionable = "The package `demo` requires `missing>=1`, but it's not installed"

    assert dependencies.runtime_dependency_health_errors(
        f"{ignored}\n{actionable}",
        project_file,
    ) == {actionable}


def test_dependency_probe_accepts_only_declared_uv_metadata_exclusions(
        tmp_path: Path,
        monkeypatch,
):
    """CI profile 探针不得把 uv 排除项以外的环境损坏静默放行。"""
    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(
        """
[tool.uv]
exclude-dependencies = [
    { package = { name = "docker" }, dependencies = ["pywin32"] },
]
""",
        encoding="utf-8",
    )
    ignored = "The package `docker` requires `pywin32>=304`, but it's not installed"
    summary = "\n".join((
        "Using Python 3.14.7 environment at: .venv",
        "Checked 189 packages in 3ms",
        "Found 1 incompatibility",
    ))
    monkeypatch.setenv("UV_PROJECT_FILE", str(project_file))
    monkeypatch.setattr(verify_runtime_profile.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        verify_runtime_profile.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=f"{summary}\n{ignored}",
            stderr="",
        ),
    )

    verify_runtime_profile.verify_uv_environment()

    actionable = "error: Failed to inspect one installed distribution"
    monkeypatch.setattr(
        verify_runtime_profile.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=f"{summary}\n{ignored}\n{actionable}",
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="Failed to inspect"):
        verify_runtime_profile.verify_uv_environment()


def test_dependency_probe_loads_windows_standard_named_pipe_capability(monkeypatch):
    """标准 Windows 必须真实加载 pywin32 与 Docker named-pipe adapter。"""
    imported = []

    def fake_import_module(name: str):
        imported.append(name)
        if name == "docker.transport":
            return SimpleNamespace(NpipeHTTPAdapter=object())
        return SimpleNamespace()

    monkeypatch.setattr(verify_runtime_profile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verify_runtime_profile.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(verify_runtime_profile.sysconfig, "get_config_var", lambda _name: 0)
    monkeypatch.setattr(
        verify_runtime_profile,
        "find_spec",
        lambda name: object() if name == "win32api" else None,
    )
    monkeypatch.setattr(verify_runtime_profile, "import_module", fake_import_module)

    verify_runtime_profile.verify_platform_profile(
        expected_profile="standard",
        expected_system="Windows",
        expected_machine="AMD64",
    )

    assert "win32api" in imported
    assert "docker.transport" in imported


def test_dependency_probe_limits_windows_free_threaded_docker_capability(monkeypatch):
    """Windows 3.14t 保留 Docker 主包，但不得伪装支持 named-pipe transport。"""
    imported = []

    def fake_import_module(name: str):
        imported.append(name)
        return SimpleNamespace()

    monkeypatch.setattr(verify_runtime_profile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verify_runtime_profile.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(verify_runtime_profile.sysconfig, "get_config_var", lambda _name: 1)
    monkeypatch.setattr(verify_runtime_profile, "find_spec", lambda _name: None)
    monkeypatch.setattr(verify_runtime_profile, "import_module", fake_import_module)

    verify_runtime_profile.verify_platform_profile(
        expected_profile="free-threaded",
        expected_system="Windows",
        expected_machine="AMD64",
    )

    assert imported == ["docker", "docker.transport"]


def test_full_dependency_probe_rejects_psycopg_python_fallback(monkeypatch):
    """V3t 构建不得把 psycopg 纯 Python 实现误认为可发布能力。"""
    modules = {
        "moviepilot_rust": SimpleNamespace(
            is_available=lambda: True,
            jieba_cut=lambda _value: ["中文", "分词"],
            zhconv_fast=lambda value, _target: value,
        ),
        "crcmod.crcmod": SimpleNamespace(_usingExtension=True),
        "psycopg": SimpleNamespace(pq=SimpleNamespace(__impl__="python")),
    }
    monkeypatch.setattr(
        dependency_doctor,
        "import_module",
        lambda name: modules.get(name, SimpleNamespace()),
    )
    monkeypatch.setattr(
        dependency_doctor.sysconfig,
        "get_config_var",
        lambda name: 1 if name == "Py_GIL_DISABLED" else None,
    )
    monkeypatch.setattr(dependency_doctor.sys, "_is_gil_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="psycopg C 实现不可用"):
        dependency_doctor.main(full=True)
