"""LivenessProbe — generic periodic reachability watchdog for any device.

The companion to :class:`AutoRecover`. AutoRecover is *reactive* — it
only acts when something nudges it (hotplug, operator, a poll). It is
explicitly **not** a watchdog. LivenessProbe is that missing watchdog:
it pings the device on a short timer and, the moment a ping fails, flips
the device ``down`` (so the bus dot goes red within a second or two
instead of waiting for the next blocking read to time out) and nudges
AutoRecover to start reconnecting.

Why this exists: a device whose disconnect is only noticed lazily — on
the next read — can sit "green but dead" for as long as that read's
timeout (e.g. a 10 s stable-weigh on a TCP scale). For an IP instrument
on a bench, that lag is the difference between "the operator sees it
drop" and "the operator wonders why the number is stale." device-guide
§5 anticipates exactly this: *"for IP devices, have your service ping on
a timer."* LivenessProbe is the reusable form of that timer.

Device-agnostic by construction — the only contract is:

* ``probe_fn() -> bool`` — a cheap, bounded reachability check. Returns
  ``True`` if the device answered, ``False`` if not. MUST have its own
  short timeout (a probe that blocks defeats the point). For a TCP
  instrument this is typically a one-line identity query
  (``check_connection`` / ``*IDN?`` / MT-SICS ``I2``).
* ``on_down(msg) -> None`` — called once on the transition from
  reachable → unreachable, to flip bus state to ``down``. Typically the
  device's ``_set_state`` partially applied to ``"down"``.
* ``recover`` (optional :class:`AutoRecover`) — ``trigger()``-ed on the
  down transition so reconnection starts immediately, and again is the
  thing that brings the device back ``ok`` (the probe does not set
  ``ok`` itself — recovery owns that, exactly as with AutoRecover).

Typical wiring (TCP scale):

    probe = LivenessProbe(
        probe_fn=lambda: station._driver is not None
                         and station._driver.check_connection(),
        on_down=lambda msg: station._set_state("down", msg),
        recover=recover,                       # the AutoRecover for this device
        interval=1.0,
        log_label=station.id,
    )
    probe.start()
    ...
    probe.stop()                               # in sim, or on teardown

Suspend it in sim the same way recovery is suspended — a fake device has
nothing to watch.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional


log = logging.getLogger(__name__)


class LivenessProbe:
    """Background reachability watchdog.

    Args:
        probe_fn: Cheap bounded reachability check, returns ``True`` if
            the device answered. MUST carry its own short timeout.
        on_down: Called as ``on_down(msg)`` once, on the reachable →
            unreachable edge. Flips bus state to ``down``.
        recover: Optional :class:`AutoRecover`. ``trigger()``-ed on the
            down edge so reconnection starts at once. The probe never
            sets ``ok`` — recovery owns the way back up.
        interval: Seconds between pings while reachable (default 1.0).
        log_label: Prefix for log lines, e.g. ``"scale:10.0.0.50"``.

    Threading:
        ``start()`` / ``stop()`` are safe from any thread. The probe runs
        on one daemon thread. It is **edge-triggered**: ``on_down`` fires
        once per reachable→unreachable transition, not every failed ping,
        so the bus isn't spammed while a device stays down. Once down, the
        probe goes quiet and lets AutoRecover drive the way back; when
        recovery succeeds (state returns to ``ok``, observed via
        ``is_ok_fn`` if provided, else assumed on the next good ping) the
        probe re-arms its edge so a *subsequent* drop is caught again.
    """

    def __init__(
        self,
        *,
        probe_fn: Callable[[], bool],
        on_down: Callable[[str], None],
        recover: Optional[object] = None,
        is_ok_fn: Optional[Callable[[], bool]] = None,
        interval: float = 1.0,
        down_msg: str = "unreachable (liveness probe)",
        log_label: str = "device",
    ):
        self._probe_fn = probe_fn
        self._on_down = on_down
        self._recover = recover
        self._is_ok_fn = is_ok_fn
        self._interval = float(interval)
        self._down_msg = str(down_msg)
        self._label = str(log_label)

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        # Edge state: True while we believe the device is reachable. Starts
        # True so the first failed ping is a genuine down edge.
        self._was_up = True

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watchdog. Idempotent — a running probe is left as-is."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._was_up = True
            self._thread = threading.Thread(
                target=self._loop, name=f"liveness-{self._label}", daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog. Safe to call when nothing is running."""
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Internals ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                up = bool(self._probe_fn())
            except Exception:
                # A raising probe == not reachable. Never let the watchdog
                # thread die on a transient error.
                log.debug("LivenessProbe[%s]: probe_fn raised", self._label, exc_info=True)
                up = False

            if up:
                # Reachable. If we were previously down and recovery has
                # brought us back (or no is_ok_fn to consult), re-arm the
                # edge so the next drop is caught.
                if not self._was_up:
                    if self._is_ok_fn is None or self._is_ok_fn():
                        self._was_up = True
            else:
                # Unreachable. Fire the down edge exactly once, then go
                # quiet and let AutoRecover own the way back up.
                if self._was_up:
                    self._was_up = False
                    log.info("LivenessProbe[%s]: device unreachable — flipping down",
                             self._label)
                    try:
                        self._on_down(self._down_msg)
                    except Exception:
                        log.exception("LivenessProbe[%s]: on_down raised", self._label)
                    if self._recover is not None:
                        try:
                            self._recover.trigger()
                        except Exception:
                            log.exception("LivenessProbe[%s]: recover.trigger raised",
                                          self._label)

            # Interruptible sleep — stop() wakes us immediately.
            if self._stop.wait(timeout=self._interval):
                return
