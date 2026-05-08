"""Test config — keeps each test isolated from the operator's bus.

Sets ``DEVICE_BUS_DETECT_CONFLICT=0`` so any test that constructs an
adapter doesn't try to talk to a real broker for conflict detection.
Tests that need conflict-detection behavior set the env var explicitly.
"""

import os

os.environ.setdefault("DEVICE_BUS_DETECT_CONFLICT", "0")
