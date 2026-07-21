"""Krepis kernel-family conformance suite (KRA-752 §10).

The family standard, mechanized: each kernel declares one
:class:`KernelProfile` in its test ``conftest.py`` and star-imports
:mod:`krepis_conformance.suite` into a test module; the suite asserts the
KRA-752 seams plus the non-deviatable KRA-779 temporal surface contract against
the imported app and repo tree so the family cannot re-drift.
Kernel-family-owned: zero sokrates-workspace and zero
framework dependencies — fastapi/fastapi_mcp/morphe are imported lazily from
the consuming kernel.
"""

from __future__ import annotations

from krepis_conformance.checks import ConformanceError
from krepis_conformance.profile import (
    AppBuilder,
    ConformanceItem,
    Deviation,
    KernelProfile,
    TemporalAppPreparer,
    TemporalProbe,
    TemporalProofMode,
)
from krepis_conformance.registry import (
    CONFORMANCE_BEARER_TOKEN,
    FAMILY_AUTH_DEFAULT,
    FAMILY_AUTH_MODES,
    FAMILY_MORPHE_TAG,
    FAMILY_PORTS,
    GOVERNED_READ_PARAM,
    ORG_SCOPE_PREFIX,
    RETIRED_TEMPORAL_QUERY_PARAMS,
    SOURCE_SURFACE_MEDIA_TYPE,
    SURFACE_OPERATION_TAG,
    TEMPORAL_QUERY_PARAM,
    UNAUTHORIZED_CODE,
)

__all__ = [
    "CONFORMANCE_BEARER_TOKEN",
    "FAMILY_AUTH_DEFAULT",
    "FAMILY_AUTH_MODES",
    "FAMILY_MORPHE_TAG",
    "FAMILY_PORTS",
    "GOVERNED_READ_PARAM",
    "ORG_SCOPE_PREFIX",
    "RETIRED_TEMPORAL_QUERY_PARAMS",
    "SOURCE_SURFACE_MEDIA_TYPE",
    "SURFACE_OPERATION_TAG",
    "TEMPORAL_QUERY_PARAM",
    "UNAUTHORIZED_CODE",
    "AppBuilder",
    "ConformanceError",
    "ConformanceItem",
    "Deviation",
    "KernelProfile",
    "TemporalAppPreparer",
    "TemporalProbe",
    "TemporalProofMode",
]
