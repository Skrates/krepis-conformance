"""Profile-contract validation: the escape hatch cannot be silent."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from krepis_conformance import Deviation, KernelProfile, TemporalProbe, TemporalProofMode
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


def test_legacy_profile_without_temporal_contract_is_rejected(
    kernel_profile: KernelProfile,
) -> None:
    legacy_constructor = cast("Any", KernelProfile)
    with pytest.raises(TypeError, match=r"temporal_probes.*prepare_temporal_app"):
        legacy_constructor(
            kernel_name=kernel_profile.kernel_name,
            repo_root=kernel_profile.repo_root,
            settings_cls=kernel_profile.settings_cls,
            build_app=kernel_profile.build_app,
            store_protocol=kernel_profile.store_protocol,
            surfaces_module=kernel_profile.surfaces_module,
        )


def test_empty_temporal_probe_tuple_is_rejected(kernel_profile: KernelProfile) -> None:
    with pytest.raises(ValueError, match="non-deviatable"):
        dataclasses.replace(kernel_profile, temporal_probes=())


def test_temporal_probes_must_be_a_tuple(kernel_profile: KernelProfile) -> None:
    with pytest.raises(TypeError, match="required tuple"):
        dataclasses.replace(kernel_profile, temporal_probes=cast("Any", []))


def test_temporal_preparer_must_be_callable(kernel_profile: KernelProfile) -> None:
    with pytest.raises(TypeError, match="preparation/seed callback"):
        dataclasses.replace(kernel_profile, prepare_temporal_app=cast("Any", None))


def test_duplicate_temporal_operation_ids_are_rejected(kernel_profile: KernelProfile) -> None:
    duplicate = (kernel_profile.temporal_probes[0], kernel_profile.temporal_probes[0])
    with pytest.raises(ValueError, match="repeat an operation_id"):
        dataclasses.replace(kernel_profile, temporal_probes=duplicate)


def test_data_delta_probe_requires_named_later_sentinel() -> None:
    with pytest.raises(ValueError, match="later_sentinel"):
        TemporalProbe(
            operation_id="surface_rows",
            earlier_as_of=date(2026, 1, 1),
            later_as_of=date(2026, 2, 1),
            proof_mode=TemporalProofMode.DATA_DELTA,
        )


def test_structural_probe_cannot_smuggle_a_sentinel() -> None:
    with pytest.raises(ValueError, match="must not declare"):
        TemporalProbe(
            operation_id="surface_rows",
            earlier_as_of=date(2026, 1, 1),
            later_as_of=date(2026, 2, 1),
            proof_mode=TemporalProofMode.STRUCTURAL,
            later_sentinel="not-applicable",
        )


def test_temporal_probe_owns_as_of_and_orders_dates() -> None:
    with pytest.raises(ValueError, match="harness-owned"):
        TemporalProbe(
            operation_id="surface_rows",
            earlier_as_of=date(2026, 1, 1),
            later_as_of=date(2026, 2, 1),
            proof_mode=TemporalProofMode.STRUCTURAL,
            query_params=(("as_of", "2026-01-01"),),
        )
    with pytest.raises(ValueError, match="must precede"):
        TemporalProbe(
            operation_id="surface_rows",
            earlier_as_of=date(2026, 2, 1),
            later_as_of=date(2026, 2, 1),
            proof_mode=TemporalProofMode.STRUCTURAL,
        )


def test_registry_ports_are_unique() -> None:
    assert len(set(FAMILY_PORTS.values())) == len(FAMILY_PORTS)
    assert set(FAMILY_PORTS) == {"zygos", "obolos", "apotheke", "chreos", "misthos", "taxis"}


def test_expected_derivations(kernel_profile: KernelProfile) -> None:
    assert kernel_profile.expected_env_prefix == "TAXIS_"
    assert kernel_profile.expected_port == 8205
    assert kernel_profile.deviation_for(ConformanceItem.ORG_SCOPE) is None
