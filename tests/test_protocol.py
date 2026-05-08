"""Tests for dorna_devices.protocol — Device Protocol + payload dataclasses.

The Protocol is structural; we just verify a duck-typed object passes
``isinstance`` and that the dataclasses round-trip the wire payloads.
"""

from __future__ import annotations

from dorna_devices.protocol import (
    Device,
    DeviceEvent,
    DeviceInfo,
    DeviceState,
)


def _good_device():
    class _Dev:
        id = "camera:abc"
        state = "ok"
        msg = ""
        def on_state_change(self, cb): pass
        def recover(self): return True
        def release(self): pass
    return _Dev()


def test_runtime_checkable_accepts_duck_typed_device():
    assert isinstance(_good_device(), Device)


def test_runtime_checkable_rejects_object_missing_methods():
    class _Half:
        id = "x"
        state = "ok"
        msg = ""
        # missing on_state_change / recover / release
    assert not isinstance(_Half(), Device)


def test_device_state_is_str_enum():
    # Mixin with str → string comparison works directly.
    assert DeviceState.OK == "ok"
    assert DeviceState.DOWN.value == "down"
    assert {DeviceState.OK, "ok"} == {DeviceState.OK}


def test_device_info_round_trip():
    payload = {
        "id": "camera:130322274110",
        "kind": "camera",
        "critical": True,
        "sim": False,
        "publisher_id": "host:1234:camera:130322274110",
        "meta": {"sn": "130322274110", "model": "D435"},
    }
    info = DeviceInfo.from_payload(payload)
    assert info.id == payload["id"]
    assert info.kind == "camera"
    assert info.critical is True
    assert info.sim is False
    assert info.meta == {"sn": "130322274110", "model": "D435"}
    assert info.to_payload() == payload


def test_device_info_defaults_for_missing_fields():
    info = DeviceInfo.from_payload({"id": "x:1", "kind": "x"})
    assert info.critical is True
    assert info.sim is False
    assert info.meta == {}


def test_device_event_round_trip():
    payload = {"state": "down", "msg": "lost", "online": True, "ts": 12345.6}
    ev = DeviceEvent.from_payload(payload)
    assert ev.state == "down"
    assert ev.msg == "lost"
    assert ev.online is True
    assert ev.ts == 12345.6
    assert ev.to_payload() == payload


def test_device_event_omits_unset_ts():
    ev = DeviceEvent(state="ok")
    out = ev.to_payload()
    assert "ts" not in out
    assert out["online"] is True


def test_device_event_lwt_shape():
    """LWT payload from the adapter side carries ``online: false``."""
    lwt = {"state": "down", "msg": "connection lost", "online": False, "ts": 1.0}
    ev = DeviceEvent.from_payload(lwt)
    assert ev.online is False
    assert ev.state == "down"
