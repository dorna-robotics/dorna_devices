"""Device protocol — the structural contract every device-side adapter expects.

This module defines:

* :class:`Device` — the structural :class:`typing.Protocol` that any
  device-shaped object (camera, robot, printer, …) must satisfy in
  order to be wrapped by :class:`MQTTDeviceAdapter`. The adapter
  duck-types: any object exposing ``id``, ``state``, ``msg``,
  ``on_state_change(cb)``, ``recover() -> bool`` and ``release()``
  works without inheriting from anything.

* :class:`DeviceState` — Enum of canonical state values published over
  the wire. The state field on :class:`Device` is still typed ``str``
  so device implementations can keep their own values; the Enum is a
  hint to callers writing new devices about the values consumers
  recognize (panel UI, orchestrator pause logic).

* :class:`DeviceInfo` / :class:`DeviceEvent` — small dataclasses that
  mirror the JSON payloads on ``device/<id>/info`` and
  ``device/<id>/state`` respectively. Useful for type hints in
  consumers; not used internally by the adapter (the adapter uses
  raw dicts so the wire format can evolve without the dataclass
  needing a release).

See the wire-format appendix in ``docs/device-guide.md`` for the
authoritative description of the topics and payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable


__all__ = [
    "Device",
    "DeviceState",
    "DeviceInfo",
    "DeviceEvent",
]


class DeviceState(str, Enum):
    """Canonical wire values for a device's ``state`` field.

    Devices may publish their own custom values, but consumers
    (orchestrator, panel UI, AutoRecover) recognize these:

    * ``OK`` — device is reachable and ready for use.
    * ``DOWN`` — device is not reachable / not usable. Critical devices
      in this state cause :class:`workspace.runtime.Runtime` to pause.
    * ``RECOVERING`` — :class:`AutoRecover` is actively retrying.
    * ``STARTING`` — initial connect in flight; pre-``OK``.
    * ``UNKNOWN`` — adapter has no state yet (panel typically hides).

    The mixin with ``str`` lets you do ``msg.state == DeviceState.OK``
    *or* ``msg.state == "ok"`` interchangeably.
    """

    OK = "ok"
    DOWN = "down"
    RECOVERING = "recovering"
    STARTING = "starting"
    UNKNOWN = "unknown"


@runtime_checkable
class Device(Protocol):
    """Structural contract for anything wrappable by ``MQTTDeviceAdapter``.

    Any object with the attributes/methods below works; you don't have
    to subclass this. The Protocol is :func:`runtime_checkable` so
    ``isinstance(obj, Device)`` works for diagnostics, but it's a
    *structural* check (presence of attributes) — it does not enforce
    method signatures at runtime.

    Required:
        id: Stable identifier for this device. Either a bare natural
            id (the adapter prepends ``<kind>:`` automatically) or
            already in ``<kind>:<natural-id>`` form.
        state: Current state. Conventionally one of :class:`DeviceState`
            values, but custom values are allowed.
        msg: Human-readable detail about the current state ("connected
            @ 192.168.0.1", "USB reset failed", …). Empty string is OK.
        on_state_change(cb): Register a callback invoked on every state
            transition with ``(state, msg)``. Called synchronously from
            the device's own thread. The adapter uses this to forward
            transitions to the bus.
        recover() -> bool: Attempt to bring the device back to ``OK``.
            Return ``True`` on success. May block; the adapter calls it
            on a worker thread so paho's network loop stays responsive.
        release() -> None: Tear the device down (close USB handle, drop
            TCP connection, …). Idempotent. Called when the operator
            issues a ``release`` command or the service shuts down.
    """

    id: str
    state: str
    msg: str

    def on_state_change(self, cb: Callable[[str, str], None]) -> None: ...
    def recover(self) -> bool: ...
    def release(self) -> None: ...


@dataclass
class DeviceInfo:
    """Mirror of the retained ``device/<id>/info`` JSON payload.

    Use to type-hint consumers that parse incoming info messages. The
    adapter does not use this dataclass internally — it builds the
    dict inline so the wire format can evolve without lockstep
    releases.
    """

    id: str
    kind: str
    critical: bool = True
    sim: bool = False
    publisher_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "DeviceInfo":
        return cls(
            id=str(payload.get("id", "")),
            kind=str(payload.get("kind", "")),
            critical=bool(payload.get("critical", True)),
            sim=bool(payload.get("sim", False)),
            publisher_id=str(payload.get("publisher_id", "")),
            meta=dict(payload.get("meta") or {}),
        )

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "critical": self.critical,
            "sim": self.sim,
            "publisher_id": self.publisher_id,
            "meta": dict(self.meta),
        }


@dataclass
class DeviceEvent:
    """Mirror of the retained ``device/<id>/state`` JSON payload.

    ``online`` distinguishes "service alive, hardware bad"
    (``state="down", online=True``) from "service dead"
    (``state="down", online=False`` — the broker LWT).
    """

    state: str
    msg: str = ""
    online: bool = True
    ts: Optional[float] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "DeviceEvent":
        return cls(
            state=str(payload.get("state", DeviceState.UNKNOWN.value)),
            msg=str(payload.get("msg", "")),
            online=bool(payload.get("online", True)),
            ts=payload.get("ts"),
        )

    def to_payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "state": self.state,
            "msg": self.msg,
            "online": self.online,
        }
        if self.ts is not None:
            out["ts"] = self.ts
        return out
