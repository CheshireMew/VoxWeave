from __future__ import annotations

import re
import tomllib
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).parents[1]


def _locked_versions() -> dict[str, str]:
    locked = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        locked[re.sub(r"[-_.]+", "-", name).casefold()] = expected
    return locked


def test_all_declared_dependencies_have_exact_lock_entries() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [
        *project["project"]["dependencies"],
        *project["project"]["optional-dependencies"]["dev"],
        *project["build-system"]["requires"],
    ]
    locked = _locked_versions()
    for requirement in declared:
        name, expected = requirement.split("==", 1)
        normalized = re.sub(r"[-_.]+", "-", name).casefold()
        assert locked[normalized] == expected


def test_transitive_runtime_dependencies_are_all_locked() -> None:
    locked = _locked_versions()
    missing: dict[str, list[str]] = {}
    for package in locked:
        if package in {"setuptools", "wheel"}:
            continue
        for value in requires(package) or []:
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            dependency = re.sub(r"[-_.]+", "-", requirement.name).casefold()
            if dependency not in locked:
                missing.setdefault(package, []).append(dependency)
    assert missing == {}


@pytest.mark.parametrize("package,expected", sorted(_locked_versions().items()))
def test_validated_environment_matches_dependency_lock(package: str, expected: str) -> None:
    if package in {"setuptools", "wheel"}:
        pytest.skip("isolated build dependency")
    try:
        actual = version(package)
    except PackageNotFoundError:
        pytest.fail(f"locked runtime package is not installed: {package}")
    assert actual == expected
