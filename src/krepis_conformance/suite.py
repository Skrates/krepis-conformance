"""The star-importable pytest conformance suite.

Adoption in a kernel is two files:

``tests/conftest.py``::

    import pytest
    from pathlib import Path
    from krepis_conformance import KernelProfile

    from taxis.api.app import create_app
    from taxis.settings import Settings
    from taxis.storage.repository import EventStore


    @pytest.fixture
    def kernel_profile() -> KernelProfile:
        def build_app(auth_mode: str, api_token: str | None) -> object:
            settings = Settings(
                environment="test",
                storage_backend="memory",
                auth_mode=auth_mode,
                api_token=api_token,
            )
            return create_app(settings)

        return KernelProfile(
            kernel_name="taxis",
            repo_root=Path(__file__).resolve().parents[1],
            settings_cls=Settings,
            build_app=build_app,
            store_protocol=EventStore,
            surfaces_module="taxis.surfaces",
        )

``tests/test_conformance.py``::

    from krepis_conformance.suite import *  # noqa: F401,F403

Every test consults the profile's declared deviations first: a declared item
skips with its reason in the pytest output (loud), an undeclared failure is
red. There is no third state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from krepis_conformance import checks
from krepis_conformance.profile import ConformanceItem
from krepis_conformance.registry import CONFORMANCE_BEARER_TOKEN

if TYPE_CHECKING:
    from krepis_conformance.profile import KernelProfile

__all__ = [
    "conformance_app",
    "conformance_bearer_app",
    "test_auth_failure_is_structured_unauthorized",
    "test_auth_mode_defaults_to_disabled",
    "test_auth_mode_enum_is_disabled_or_bearer",
    "test_compose_declares_healthcheck",
    "test_deploy_is_digest_pinned",
    "test_env_prefix_is_kernel_name",
    "test_event_store_protocol_declares_lifecycle",
    "test_family_port_is_registered_and_bound",
    "test_governed_read_selector_is_the_family_param",
    "test_mcp_mounts_the_shared_pruned_shape",
    "test_morphe_boot_assertion_is_dynamic",
    "test_morphe_pin_is_the_family_tag",
    "test_parameterized_routes_are_org_scoped",
    "test_sokrates_bundle_is_complete",
]


def _respect_deviation(profile: KernelProfile, item: ConformanceItem) -> None:
    deviation = profile.deviation_for(item)
    if deviation is not None:
        pytest.skip(f"declared deviation [{item.value}]: {deviation.reason}")


@pytest.fixture
def conformance_app(kernel_profile: KernelProfile) -> object:
    """A fresh memory-backed kernel app with auth disabled."""
    return kernel_profile.build_app("disabled", None)


@pytest.fixture
def conformance_bearer_app(kernel_profile: KernelProfile) -> object:
    """A fresh memory-backed kernel app in bearer mode with the probe token."""
    return kernel_profile.build_app("bearer", CONFORMANCE_BEARER_TOKEN)


# ── §1 auth contract ───────────────────────────────────────────────────


def test_auth_mode_enum_is_disabled_or_bearer(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.AUTH_MODE)
    checks.check_auth_mode_enum(kernel_profile)


def test_auth_mode_defaults_to_disabled(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.AUTH_MODE)
    checks.check_auth_mode_default(kernel_profile)


# ── §2 env prefix ──────────────────────────────────────────────────────


def test_env_prefix_is_kernel_name(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.ENV_PREFIX)
    checks.check_env_prefix(kernel_profile)


# ── §3 MCP surface ─────────────────────────────────────────────────────


def test_mcp_mounts_the_shared_pruned_shape(
    kernel_profile: KernelProfile, conformance_app: object
) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.MCP_SURFACE)
    checks.check_mcp_surface(kernel_profile, conformance_app)


# ── §4 Morphe ──────────────────────────────────────────────────────────


def test_morphe_pin_is_the_family_tag(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.MORPHE_PIN)
    checks.check_morphe_pin(kernel_profile)


def test_morphe_boot_assertion_is_dynamic(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.MORPHE_PIN)
    checks.check_morphe_boot_assertion(kernel_profile)


# ── §5 parent scope ────────────────────────────────────────────────────


def test_parameterized_routes_are_org_scoped(
    kernel_profile: KernelProfile, conformance_app: object
) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.ORG_SCOPE)
    checks.check_org_scope(kernel_profile, conformance_app)


# ── §6 store protocol ──────────────────────────────────────────────────


def test_event_store_protocol_declares_lifecycle(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.STORE_PROTOCOL)
    checks.check_store_protocol(kernel_profile)


# ── §7 structured auth errors ──────────────────────────────────────────


def test_auth_failure_is_structured_unauthorized(
    kernel_profile: KernelProfile, conformance_bearer_app: object
) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.AUTH_ERRORS)
    checks.check_auth_errors(kernel_profile, conformance_bearer_app)


# ── §8 deploy + port ───────────────────────────────────────────────────


def test_deploy_is_digest_pinned(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.DEPLOY)
    checks.check_deploy_digest_pin(kernel_profile)


def test_compose_declares_healthcheck(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.DEPLOY)
    checks.check_compose_healthcheck(kernel_profile)


def test_family_port_is_registered_and_bound(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.PORT)
    checks.check_family_port(kernel_profile)


# ── §9 bundle ──────────────────────────────────────────────────────────


def test_sokrates_bundle_is_complete(kernel_profile: KernelProfile) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.BUNDLE)
    checks.check_sokrates_bundle(kernel_profile)


# ── §11 governed-read selector convention ──────────────────────────────


def test_governed_read_selector_is_the_family_param(
    kernel_profile: KernelProfile, conformance_app: object
) -> None:
    _respect_deviation(kernel_profile, ConformanceItem.GOVERNED_PARAMS)
    checks.check_governed_read_params(kernel_profile, conformance_app)
