from __future__ import annotations

import json
from pathlib import Path

from .model import ReconciliationError


class TransientFailure(RuntimeError):
    pass


class PermanentFailure(RuntimeError):
    pass


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


class FakePlatform:
    """A durable local API simulation. Never contacts an advertising service."""

    def __init__(self, path: Path, faults: dict | None = None):
        self.path = path
        self.faults = faults or {}
        if not path.exists():
            atomic_json(path, {"records": {}, "idempotency": {}, "attempts": {}, "unique_effects": 0})

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def read_booking(self, booking_id):
        return self.state()["records"].get(booking_id)

    def submit(self, action):
        state = self.state()
        operation_id = action["operation_id"]
        state["attempts"][operation_id] = state["attempts"].get(operation_id, 0) + 1
        attempt = state["attempts"][operation_id]
        atomic_json(self.path, state)
        if operation_id in state["idempotency"]:
            return state["idempotency"][operation_id]
        fault = self.faults.get(action["booking_id"], {})
        if fault.get("mode") == "permanent":
            raise PermanentFailure("Synthetic permanent platform rejection.")
        if fault.get("mode") == "transient" and attempt <= fault.get("failures", 1):
            raise TransientFailure("Synthetic temporary platform failure.")
        existing = state["records"].get(action["booking_id"])
        if action["type"] == "create" and existing:
            raise PermanentFailure("Booking already exists; reconcile rather than overwrite it.")
        if action["type"] == "restate" and not existing:
            raise PermanentFailure("Cannot restate a missing booking.")
        record = {"booking_id": action["booking_id"], "customer_id": action["customer_id"],
                  "value_cents": action["value_cents"], "conversion_time": action["conversion_time"],
                  "click_id": action["click_id"], "click_source": action["click_source"],
                  "request_status": "accepted", "matched": None, "reported_attribution": None}
        state["records"][action["booking_id"]] = record
        response = {"status": "accepted", "operation_id": operation_id}
        state["idempotency"][operation_id] = response
        state["unique_effects"] += 1
        if fault.get("mode") == "corrupt_readback":
            state["records"][action["booking_id"]]["value_cents"] += 1
        atomic_json(self.path, state)
        if fault.get("mode") == "disconnect_after_accept" and attempt == 1:
            raise TransientFailure("Synthetic response loss after platform acceptance.")
        return response


def verified_record(platform: FakePlatform, action: dict) -> dict:
    record = platform.read_booking(action["booking_id"])
    expected = {"booking_id": action["booking_id"], "customer_id": action["customer_id"],
                "value_cents": action["value_cents"], "conversion_time": action["conversion_time"],
                "click_id": action["click_id"], "click_source": action["click_source"],
                "request_status": "accepted", "matched": None, "reported_attribution": None}
    if record != expected:
        raise ReconciliationError("Accepted response did not match independent platform readback.")
    return record
