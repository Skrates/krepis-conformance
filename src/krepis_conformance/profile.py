"""The per-kernel conformance contract.

Each kernel declares exactly one :class:`KernelProfile` in its own test
``conftest.py`` (as a ``kernel_profile`` fixture). The profile is the ONLY
kernel-specific input the suite consumes — everything else is the family
standard in :mod:`krepis_conformance.registry`.

Deviations are the escape hatch for the original KRA-752 items: a kernel that
genuinely cannot satisfy one (the sanctioned case: zygos book-scope, where a
book is not an org) declares it WITH a reason. The temporal KRA-779 section is
different by design: it has no :class:`ConformanceItem`, so it cannot be
deviated. Every ``surfaces`` GET must have a behavioral probe.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from krepis_conformance.registry import FAMILY_PORTS

if TYPE_CHECKING:
    from pathlib import Path

type AppBuilder = Callable[[str, str | None], object]
"""``(auth_mode, api_token) -> ASGI app`` — a fresh, memory-backed kernel app."""

type TemporalAppPreparer = Callable[[object, Any], Mapping[str, str]]
"""Prepare/seed a fresh app through its real HTTP boundary.

The arguments are ``(app, TestClient)``. The returned mapping supplies concrete
values for OpenAPI path parameters (for example a server-generated ``book_id``).
It is deliberately not a temporal-resolution hook: dates and expected proof
semantics live immutably on each :class:`TemporalProbe`.
"""


class TemporalProofMode(StrEnum):
    """The three exhaustive ways a surface can prove effective-date behavior."""

    DATA_DELTA = "data-delta"
    BEFORE_BIRTH = "before-birth"
    STRUCTURAL = "structural"


@dataclass(frozen=True, slots=True)
class TemporalProbe:
    """One operation-complete behavioral proof for a ``surfaces`` GET operation.

    The harness discovers the route path from ``operation_id`` in the live
    OpenAPI document. ``query_params`` carries only independent axes such as
    ``after_sequence``; the harness alone supplies ``as_of`` for both dates.
    """

    operation_id: str
    earlier_as_of: date
    later_as_of: date
    proof_mode: TemporalProofMode
    later_sentinel: str | None = None
    query_params: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.operation_id.strip():
            raise ValueError("temporal probe operation_id must be non-empty")
        if not isinstance(self.earlier_as_of, date) or not isinstance(self.later_as_of, date):
            raise TypeError("temporal probe dates must be datetime.date values")
        if self.earlier_as_of >= self.later_as_of:
            raise ValueError("temporal probe earlier_as_of must precede later_as_of")
        if not isinstance(self.proof_mode, TemporalProofMode):
            raise TypeError("temporal probe proof_mode must be a TemporalProofMode")
        if not isinstance(self.query_params, tuple):
            raise TypeError("temporal probe query_params must be a tuple")

        seen_query_names: set[str] = set()
        for item in self.query_params:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("temporal probe query_params entries must be (name, value) tuples")
            name, value = item
            if not isinstance(name, str) or not name:
                raise TypeError("temporal probe query parameter names must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError("temporal probe query parameter values must be strings")
            if name == "as_of":
                raise ValueError("temporal probe query_params cannot supply harness-owned as_of")
            if name in seen_query_names:
                raise ValueError(f"duplicate temporal probe query parameter {name!r}")
            seen_query_names.add(name)

        sentinel = self.later_sentinel
        if self.proof_mode is TemporalProofMode.DATA_DELTA:
            if sentinel is None or not sentinel.strip():
                raise ValueError("data-delta temporal probes require a named later_sentinel")
        elif sentinel is not None:
            raise ValueError(
                f"{self.proof_mode.value} temporal probes must not declare a later_sentinel"
            )


class ConformanceItem(StrEnum):
    """The deviatable seams of the KRA-752 standard (§ numbers per the ticket)."""

    AUTH_MODE = "auth-mode"  # §1 enum {disabled, bearer} + default disabled
    ENV_PREFIX = "env-prefix"  # §2 env prefix == kernel name
    MCP_SURFACE = "mcp-surface"  # §3 fastapi_mcp mount, _PrunedFastApiMCP shape
    MORPHE_PIN = "morphe-pin"  # §4 single py-v tag pin + dynamic boot assertion
    ORG_SCOPE = "org-scope"  # §5 /orgs/{org_id} parent scope
    STORE_PROTOCOL = "store-protocol"  # §6 EventStore declares close()
    AUTH_ERRORS = "auth-errors"  # §7 structured ERR-UNAUTHORIZED
    DEPLOY = "deploy"  # §8 digest-pinned image + healthcheck
    PORT = "port"  # §8 unique family port registry
    BUNDLE = "bundle"  # §9 integrations/sokrates completeness
    GOVERNED_PARAMS = "governed-params"  # §11 governed-read selector spelled include_pii


@dataclass(frozen=True, slots=True)
class Deviation:
    """One declared, reasoned departure from a standard item."""

    item: ConformanceItem
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"deviation from {self.item.value!r} requires a documented reason — "
                "an unreasoned deviation is a silent skip, which the family forbids"
            )


@dataclass(frozen=True, slots=True)
class KernelProfile:
    """Everything the conformance suite needs to know about one kernel.

    ``build_app`` must return a fresh application wired to the in-memory
    backend: ``build_app("disabled", None)`` for the open probes and
    ``build_app("bearer", token)`` for the behavioural auth checks. The app is
    expected to expose its MCP handle as ``app.state.mcp`` (the family
    ``create_app`` convention).

    ``prepare_temporal_app`` runs inside a live ``TestClient`` context for a
    second fresh app. It installs a runtime test signer when the app builder
    does not, seeds through append endpoints, and returns only concrete values
    for path placeholders. It never chooses or snaps dates: each immutable
    ``TemporalProbe`` carries that contract directly.
    """

    kernel_name: str
    repo_root: Path
    settings_cls: type
    build_app: AppBuilder
    store_protocol: type
    surfaces_module: str
    temporal_probes: tuple[TemporalProbe, ...]
    prepare_temporal_app: TemporalAppPreparer
    protected_probe_path: str = "/orgs"
    deviations: tuple[Deviation, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.kernel_name not in FAMILY_PORTS:
            known = ", ".join(sorted(FAMILY_PORTS))
            raise ValueError(
                f"kernel_name {self.kernel_name!r} is not in the family port registry ({known}); "
                "adding a kernel is a family decision made in krepis_conformance.registry"
            )
        if not self.repo_root.is_dir():
            raise ValueError(f"repo_root {self.repo_root} is not a directory")
        if not self.protected_probe_path.startswith("/"):
            raise ValueError("protected_probe_path must be an absolute route path")
        if not isinstance(self.temporal_probes, tuple):
            raise TypeError("temporal_probes must be a required tuple of TemporalProbe cases")
        if not self.temporal_probes:
            raise ValueError(
                "temporal_probes must contain one case for every surfaces GET operation; "
                "the as_of section is non-deviatable"
            )
        if not all(isinstance(probe, TemporalProbe) for probe in self.temporal_probes):
            raise TypeError("temporal_probes must contain only TemporalProbe cases")
        operation_ids = [probe.operation_id for probe in self.temporal_probes]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("temporal_probes must not repeat an operation_id")
        if not callable(self.prepare_temporal_app):
            raise TypeError("prepare_temporal_app must be a preparation/seed callback")
        seen: set[ConformanceItem] = set()
        for deviation in self.deviations:
            if deviation.item in seen:
                raise ValueError(f"duplicate deviation declared for {deviation.item.value!r}")
            seen.add(deviation.item)

    @property
    def expected_env_prefix(self) -> str:
        """The §2 env prefix implied by the kernel name."""
        return f"{self.kernel_name.upper()}_"

    @property
    def expected_port(self) -> int:
        """The §8 registry port bound to this kernel."""
        return FAMILY_PORTS[self.kernel_name]

    def deviation_for(self, item: ConformanceItem) -> Deviation | None:
        """Return the declared deviation for ``item``, if any."""
        for deviation in self.deviations:
            if deviation.item is item:
                return deviation
        return None


__all__ = [
    "AppBuilder",
    "ConformanceItem",
    "Deviation",
    "KernelProfile",
    "TemporalAppPreparer",
    "TemporalProbe",
    "TemporalProofMode",
]
