# dorna_devices

Device protocol + MQTT publisher + auto-recovery primitives for the
Dorna orchestrator. This is the small device-side library that hardware
services (camera, printer, robot, …) install so they can publish their
state to the orchestrator's MQTT bus and answer recover / release
commands — without dragging in the full Dorna workspace SDK.

The package depends only on the standard library plus `paho-mqtt`, so a
device Pi can install just `dorna_devices` and a transport client.

## Install

From a checkout (editable, recommended for now):

```sh
pip install -e .
```

Or once published:

```sh
pip install dorna_devices
```

## Quick start

A minimal device that meets the protocol and publishes itself to the
bus:

```python
from dorna_devices import MQTTDeviceAdapter

class MyDevice:
    def __init__(self):
        self.id = "printer:lab-01"
        self.state = "ok"
        self.msg = "ready"
        self._cb = None
    def on_state_change(self, cb): self._cb = cb
    def recover(self) -> bool: return True
    def release(self): pass

dev = MyDevice()
adapter = MQTTDeviceAdapter(dev, kind="printer", critical=True)
# Adapter is now publishing device/printer:lab-01/info + .../state
# on the bus, and listening for cmd/recover and cmd/release replies.
```

For exponential-backoff auto-recovery (USB hotplug, brief network drops,
etc.):

```python
from dorna_devices import AutoRecover

recover = AutoRecover(
    recover_fn=dev.recover,
    set_status=lambda state, msg: setattr(dev, "state", state),
    log_label=dev.id,
)
recover.trigger()  # idempotent; safe to call from hotplug events
```

## Documentation

See [`docs/device-guide.md`](docs/device-guide.md) for the full
integration playbook — device contract, adapter pattern, ID convention,
the MQTT wire protocol, and end-to-end examples.

## Layout

```
dorna_devices/
  protocol.py    # Device Protocol + DeviceState/Info/Event types
  adapter.py     # MQTTDeviceAdapter — publisher side of the wire spec
  recovery.py    # AutoRecover — generic backoff loop
```

## License

MIT (or whatever the parent project ships with — TODO once the repo is
published).
