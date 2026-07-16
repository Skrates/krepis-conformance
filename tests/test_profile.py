"""Profile-contract validation: the escape hatch cannot be silent."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from krepis_conformance import Deviation, KernelProfile
from krepis_conformance.profile import ConformanceItem
from krepis_conformance.registry import FAMILY_PORTS


def test_unreasoned_deviation_is_rejected() -> None:
    with pytest.raises(ValueError, match="documented reason"):
        Deviation(item=ConformanceItem.ORG_SCOPE, reason="   ")


def test_unknown_kernel_name_is_rejected(kernel_profile: KernelProfile) -> None:
    with pytest.raises(ValueError, match="port registry"):
        dataclasses.replace(kernel_profile, kernel_name="hermes")


def test_duplicate_deviation_items_are_rejected(kernel_profile: KernelProfile) -> None:
    deviations = (
        Deviation(item=ConformanceItem.ORG_SCOPE, reason="one"),
        Deviation(item=ConformanceItem.ORG_SCOPE, reason="two"),
    )
    with pytest.raises(ValueError, match="duplicate"):
        dataclasses.replace(kernel_profile, deviations=deviations)


def test_missing_repo_root_is_rejected(kernel_profile: KernelProfile) -> None:
    with pytest.raises(ValueError, match="directory"):
        dataclasses.replace(kernel_profile, repo_root=Path("/definitely/not/a/dir"))


def test_relative_probe_path_is_rejected(kernel_profile: KernelProfile) -> None:
    with pytest.raises(ValueError, match="absolute route path"):
        dataclasses.replace(kernel_profile, protected_probe_path="orgs")


def test_registry_ports_are_unique() -> None:
    assert len(set(FAMILY_PORTS.values())) == len(FAMILY_PORTS)
    assert set(FAMILY_PORTS) == {"zygos", "obolos", "apotheke", "chreos", "misthos", "taxis"}


def test_expected_derivations(kernel_profile: KernelProfile) -> None:
    assert kernel_profile.expected_env_prefix == "TAXIS_"
    assert kernel_profile.expected_port == 8205
    assert kernel_profile.deviation_for(ConformanceItem.ORG_SCOPE) is None
