"""Dogfood: this repo consumes its own suite exactly as a kernel would.

The conforming fake kernel (see ``conftest.py``) must pass every conformance
test — a star import, nothing else. If a check regresses into failing a
conforming kernel, this file turns red.
"""

from krepis_conformance.suite import *  # noqa: F403
