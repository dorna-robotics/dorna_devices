"""Device protocol + MQTT publisher + auto-recovery for Dorna devices.

This is the device-side library: the primitives a service that owns a
piece of hardware (camera, printer, robot, …) needs in order to publish
its state to the orchestrator's MQTT bus and respond to recover /
release commands.

The package depends only on the standard library plus ``paho-mqtt`` so
device services can install it without pulling in the workspace
orchestrator and its scientific-Python dependencies.

Public API:

* :class:`Device`, :class:`DeviceState`, :class:`DeviceInfo`,
  :class:`DeviceEvent` — protocol shapes describing the contract.
* :class:`MQTTDeviceAdapter` — wraps any ``Device``-shaped object and
  runs the publisher half of the spec.
* :class:`AutoRecover` — generic exponential-backoff recovery loop.
* :class:`DevicePublisherConflict`, :func:`detect_publisher_conflict`,
  :func:`make_publisher_id` — guard against two services claiming the
  same device id on the bus.

See ``docs/device-guide.md`` for the integration playbook (device
contract, adapter pattern, ID convention, end-to-end examples).
"""

from __future__ import annotations

from dorna_devices.protocol import (
    Device,
    DeviceState,
    DeviceInfo,
    DeviceEvent,
)
from dorna_devices.adapter import (
    MQTTDeviceAdapter,
    DevicePublisherConflict,
    detect_publisher_conflict,
    make_publisher_id,
    DEFAULT_BROKER_HOST,
    DEFAULT_BROKER_PORT,
)
from dorna_devices.recovery import AutoRecover

__all__ = [
    # protocol shapes
    "Device",
    "DeviceState",
    "DeviceInfo",
    "DeviceEvent",
    # publisher
    "MQTTDeviceAdapter",
    "DevicePublisherConflict",
    "detect_publisher_conflict",
    "make_publisher_id",
    "DEFAULT_BROKER_HOST",
    "DEFAULT_BROKER_PORT",
    # recovery
    "AutoRecover",
]

__version__ = "0.1.0"
