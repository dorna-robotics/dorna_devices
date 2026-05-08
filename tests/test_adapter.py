"""Tests for dorna_devices.adapter.MQTTDeviceAdapter.

These tests stub out paho.mqtt.client.Client with a fake so no real
broker is needed. They cover:

* ID normalization (kind prefix prepended when missing).
* publisher_id format.
* DevicePublisherConflict shape.
* Adapter publishes info + state on connect, and a final
  ``online: false`` on close().
* Command dispatch routes to ``device.recover()`` / ``release()`` and
  publishes a reply payload.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

import pytest

import dorna_devices.adapter as adapter_mod
from dorna_devices import (
    DevicePublisherConflict,
    MQTTDeviceAdapter,
    make_publisher_id,
)


# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeMessage:
    def __init__(self, topic: str, payload: bytes):
        self.topic = topic
        self.payload = payload


class FakeClient:
    """Minimal stand-in for paho.mqtt.client.Client used by the adapter."""

    def __init__(self, *args, **kwargs):
        self.client_id = kwargs.get("client_id", "")
        self.on_connect: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_disconnect: Optional[Callable] = None
        self.lwt: Optional[Tuple[str, str, int, bool]] = None
        self.published: List[Tuple[str, str, int, bool]] = []
        self.subscribed: List[Tuple[str, int]] = []
        self.connected = False
        self.loop_started = False
        self.disconnected = False

    def will_set(self, topic, payload, qos=0, retain=False):
        self.lwt = (topic, payload, qos, retain)

    def connect(self, host, port, keepalive=30):
        self.connected = True

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True
        self.connected = False

    def subscribe(self, topic_or_list, qos=0):
        if isinstance(topic_or_list, list):
            self.subscribed.extend(topic_or_list)
        elif isinstance(topic_or_list, tuple):
            self.subscribed.append(topic_or_list)
        else:
            self.subscribed.append((topic_or_list, qos))

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    # Test helpers ────────────────────────────────────────────────────────
    def fire_connect(self):
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def fire_message(self, topic: str, payload: bytes):
        if self.on_message:
            self.on_message(self, None, FakeMessage(topic, payload))


class FakeDevice:
    """Minimum object that satisfies the Device protocol."""

    def __init__(self, device_id: str = "abc-001"):
        self.id = device_id
        self.state = "ok"
        self.msg = ""
        self._cb: Optional[Callable[[str, str], None]] = None
        self.recover_calls = 0
        self.release_calls = 0
        self.recover_returns = True

    def on_state_change(self, cb):
        self._cb = cb

    def recover(self) -> bool:
        self.recover_calls += 1
        return self.recover_returns

    def release(self) -> None:
        self.release_calls += 1

    # Drive a state transition for the test ───────────────────────────────
    def set_state(self, state: str, msg: str = ""):
        self.state = state
        self.msg = msg
        if self._cb:
            self._cb(state, msg)


@pytest.fixture
def patch_mqtt(monkeypatch):
    """Replace paho's Client class used inside adapter with FakeClient.

    Returns the FakeClient instance after the adapter constructs one
    (only one client per test).
    """
    created: list[FakeClient] = []

    class _ClientFactory(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    # patch the symbol used by adapter.py: ``mqtt.Client``
    monkeypatch.setattr(adapter_mod.mqtt, "Client", _ClientFactory)
    return created


# ── Tests ────────────────────────────────────────────────────────────────


def test_make_publisher_id_format():
    pid = make_publisher_id("camera:abc")
    parts = pid.split(":")
    assert len(parts) == 4               # host:pid:camera:abc
    assert parts[-2:] == ["camera", "abc"]
    # second segment is numeric (PID)
    assert parts[1].isdigit()


def test_id_normalization_prepends_kind(patch_mqtt):
    dev = FakeDevice("abc-001")
    MQTTDeviceAdapter(dev, kind="camera")
    client = patch_mqtt[0]
    # LWT goes to the canonical topic with the kind prefix.
    assert client.lwt is not None
    topic = client.lwt[0]
    assert topic == "device/camera:abc-001/state"


def test_id_already_prefixed_is_left_alone(patch_mqtt):
    dev = FakeDevice("camera:already-prefixed")
    MQTTDeviceAdapter(dev, kind="camera")
    client = patch_mqtt[0]
    assert client.lwt[0] == "device/camera:already-prefixed/state"


def test_publishes_info_and_state_on_connect(patch_mqtt):
    dev = FakeDevice("abc")
    MQTTDeviceAdapter(dev, kind="printer", critical=True, sim=False, meta={"sn": "x"})
    client = patch_mqtt[0]

    client.fire_connect()

    info_msgs = [m for m in client.published if m[0] == "device/printer:abc/info"]
    state_msgs = [m for m in client.published if m[0] == "device/printer:abc/state"]
    assert len(info_msgs) == 1
    assert len(state_msgs) == 1

    info_payload = json.loads(info_msgs[0][1])
    assert info_payload["id"] == "printer:abc"
    assert info_payload["kind"] == "printer"
    assert info_payload["critical"] is True
    assert info_payload["sim"] is False
    assert info_payload["meta"] == {"sn": "x"}
    assert info_payload["publisher_id"].endswith(":printer:abc")

    state_payload = json.loads(state_msgs[0][1])
    assert state_payload["state"] == "ok"
    assert state_payload["online"] is True

    # Subscribed for cmd/+
    assert any("cmd" in s[0] for s in client.subscribed)


def test_state_change_publishes(patch_mqtt):
    dev = FakeDevice("abc")
    MQTTDeviceAdapter(dev, kind="printer")
    client = patch_mqtt[0]
    client.fire_connect()

    # Drive a transition
    dev.set_state("down", "lost")
    state_msgs = [m for m in client.published if m[0] == "device/printer:abc/state"]
    # 1 from on_connect + 1 from set_state
    assert len(state_msgs) == 2
    last = json.loads(state_msgs[-1][1])
    assert last["state"] == "down"
    assert last["msg"] == "lost"
    assert last["online"] is True


def test_recover_command_invokes_device_and_replies(patch_mqtt):
    dev = FakeDevice("abc")
    MQTTDeviceAdapter(dev, kind="printer")
    client = patch_mqtt[0]
    client.fire_connect()

    # Send recover cmd
    cmd_payload = json.dumps({"req_id": "r1"}).encode()
    client.fire_message("device/printer:abc/cmd/recover", cmd_payload)

    # The adapter dispatches on a worker thread; wait for the reply.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        replies = [
            m for m in client.published
            if m[0] == "device/printer:abc/cmd/recover/reply"
        ]
        if replies:
            break
        time.sleep(0.01)
    assert dev.recover_calls == 1
    replies = [
        m for m in client.published
        if m[0] == "device/printer:abc/cmd/recover/reply"
    ]
    assert len(replies) == 1
    reply = json.loads(replies[0][1])
    assert reply["req_id"] == "r1"
    assert reply["ok"] is True


def test_release_command_invokes_device(patch_mqtt):
    dev = FakeDevice("abc")
    MQTTDeviceAdapter(dev, kind="printer")
    client = patch_mqtt[0]
    client.fire_connect()

    client.fire_message(
        "device/printer:abc/cmd/release",
        json.dumps({"req_id": "rel-1"}).encode(),
    )
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if dev.release_calls == 1:
            break
        time.sleep(0.01)
    assert dev.release_calls == 1


def test_close_publishes_offline_and_disconnects(patch_mqtt):
    dev = FakeDevice("abc")
    a = MQTTDeviceAdapter(dev, kind="printer")
    client = patch_mqtt[0]
    client.fire_connect()

    a.close()
    # Final state publish should carry online=false.
    state_msgs = [m for m in client.published if m[0] == "device/printer:abc/state"]
    last = json.loads(state_msgs[-1][1])
    assert last["online"] is False
    assert last["state"] == "down"
    assert client.disconnected is True


def test_close_is_idempotent(patch_mqtt):
    dev = FakeDevice("abc")
    a = MQTTDeviceAdapter(dev, kind="printer")
    a.close()
    a.close()  # must not raise


def test_lwt_set_with_online_false(patch_mqtt):
    dev = FakeDevice("abc")
    MQTTDeviceAdapter(dev, kind="printer")
    client = patch_mqtt[0]
    assert client.lwt is not None
    topic, payload, qos, retain = client.lwt
    assert topic == "device/printer:abc/state"
    payload_obj = json.loads(payload)
    assert payload_obj["online"] is False
    assert payload_obj["state"] == "down"
    assert retain is True


def test_device_publisher_conflict_carries_ids():
    exc = DevicePublisherConflict("camera:1", "owner-pub", "our-pub")
    assert exc.device_id == "camera:1"
    assert exc.owner_publisher_id == "owner-pub"
    assert exc.our_publisher_id == "our-pub"
    msg = str(exc)
    assert "camera:1" in msg
    assert "owner-pub" in msg


def test_set_sim_republishes_info(patch_mqtt):
    dev = FakeDevice("abc")
    a = MQTTDeviceAdapter(dev, kind="printer", sim=False)
    client = patch_mqtt[0]
    client.fire_connect()

    initial_info = [m for m in client.published if m[0] == "device/printer:abc/info"]
    assert len(initial_info) == 1
    assert json.loads(initial_info[0][1])["sim"] is False

    a.set_sim(True)
    info_msgs = [m for m in client.published if m[0] == "device/printer:abc/info"]
    assert len(info_msgs) == 2
    assert json.loads(info_msgs[-1][1])["sim"] is True

    # No-op when called with the same value.
    a.set_sim(True)
    info_msgs = [m for m in client.published if m[0] == "device/printer:abc/info"]
    assert len(info_msgs) == 2


def test_constructor_raises_when_device_id_missing(patch_mqtt):
    class NoId:
        id = ""
        state = "ok"
        msg = ""
        def on_state_change(self, cb): pass
        def recover(self): return True
        def release(self): pass

    with pytest.raises(ValueError):
        MQTTDeviceAdapter(NoId(), kind="x")
