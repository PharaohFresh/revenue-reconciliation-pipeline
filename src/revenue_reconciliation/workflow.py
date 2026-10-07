from __future__ import annotations

import json
from pathlib import Path

from .model import ReconciliationError, digest, empty_ledger, validate_plan
from .platform import FakePlatform, PermanentFailure, TransientFailure, atomic_json, verified_record


def read_state(root: Path) -> dict:
    path = root / "ledger.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty_ledger()


def immutable_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ReconciliationError("Immutable execution evidence changed.")
    else:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")


def expected_state(plan, completed):
    deliveries = dict(plan["before"]["deliveries"])
    for action in plan["actions"]:
        if action["operation_id"] in completed:
            deliveries[action["booking_id"]] = {
                "booking_id": action["booking_id"], "customer_id": action["customer_id"],
                "value_cents": action["value_cents"], "conversion_time": action["conversion_time"],
                "click_id": action["click_id"], "click_source": action["click_source"],
                "request_status": "accepted", "matched": None, "reported_attribution": None,
            }
    operation_ids = {action["operation_id"] for action in plan["actions"]}
    if set(completed) - operation_ids:
        raise ReconciliationError("Checkpoint contains an unapproved operation.")
    return {"version": 1, "events": plan["events"], "deliveries": deliveries,
            "last_export_as_of": plan["export_as_of"],
            "active": {"plan_sha256": digest(plan), "completed_operations": sorted(completed),
                       "status": "complete" if set(completed) == operation_ids else "partial"}}


def execute(plan, approval, export: Path, metadata: Path, root: Path, *, faults=None, max_attempts=3):
    plan_hash = validate_plan(plan, export, metadata)
    if not isinstance(approval, dict) or approval.get("plan_sha256") != plan_hash or not approval.get("approver"):
        raise ReconciliationError("Approval must cover this exact export, metadata, ledger and action set.")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10:
        raise ReconciliationError("Retry bound must be an integer from one to ten.")
    current = read_state(root)
    active = current.get("active")
    if active and active.get("plan_sha256") == plan_hash:
        completed = set(active["completed_operations"])
        if current != expected_state(plan, completed):
            raise ReconciliationError("Working ledger drifted outside the approved checkpoint.")
    else:
        if current != plan["before"]:
            raise ReconciliationError("Ledger changed after planning.")
        completed = set()
    # Evidence is durable before working-state or simulated API mutations.
    evidence = root / "evidence" / plan_hash
    immutable_json(evidence / "plan.json", plan)
    immutable_json(evidence / "approval.json", approval)
    immutable_json(evidence / "before-ledger.json", plan["before"])
    platform = FakePlatform(root / "fake-platform.json", faults)
    pending_by_booking = {action["booking_id"]: action for action in plan["actions"]
                          if action["operation_id"] not in completed}
    for booking_id, record in current["deliveries"].items():
        observed = platform.read_booking(booking_id)
        if observed != record:
            # A response may have been lost after an approved restatement took effect.
            # Permit recovery only when the exact operation exists in the fake API's
            # idempotency record and its value/time/identity read back correctly.
            pending = pending_by_booking.get(booking_id)
            if not pending or pending["operation_id"] not in platform.state()["idempotency"]:
                raise ReconciliationError("Previously accepted platform state drifted; reconcile before more delivery.")
            verified_record(platform, pending)
    atomic_json(root / "ledger.json", expected_state(plan, completed))
    failures = {}
    for action in plan["actions"]:
        operation_id = action["operation_id"]
        if operation_id in completed:
            continue
        for attempt in range(1, max_attempts + 1):
            try:
                response = platform.submit(action)
                if response != {"status": "accepted", "operation_id": operation_id}:
                    raise ReconciliationError("Unexpected platform acknowledgement.")
                verified_record(platform, action)
                completed.add(operation_id)
                atomic_json(root / "ledger.json", expected_state(plan, completed))
                break
            except TransientFailure as exc:
                if attempt == max_attempts:
                    failures[operation_id] = str(exc)
            except (PermanentFailure, ReconciliationError) as exc:
                failures[operation_id] = str(exc)
                break
    final = read_state(root)
    if final != expected_state(plan, completed):
        raise ReconciliationError("Final ledger readback failed.")
    for action in plan["actions"]:
        if action["operation_id"] in completed:
            verified_record(platform, action)
    report = {"status": final["active"]["status"], "plan_sha256": plan_hash,
              "payment_event_count": len(final["events"]), "booking_count": len(plan["bookings"]),
              "completed_operations": len(completed), "planned_operations": len(plan["actions"]),
              "failures": failures, "holds": plan["holds"],
              "source_service_value_cents": sum(x["service_value_cents"] for x in plan["bookings"].values()),
              "source_net_service_cash_cents": sum(x["net_service_cash_cents"] for x in plan["bookings"].values()),
              "accepted_conversion_value_cents": sum(x["value_cents"] for x in final["deliveries"].values()),
              "matching_proven": False, "reported_attribution_proven": False,
              "platform": "durable local fake API; no external request", "data_origin": "synthetic"}
    attempts_dir = evidence / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    sequence = len(list(attempts_dir.glob("*.json"))) + 1
    immutable_json(attempts_dir / f"{sequence:04d}.json", report)
    return report
