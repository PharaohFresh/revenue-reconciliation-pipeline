from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


FIELDS = {"payment_id", "booking_id", "customer_id", "kind", "occurred_at", "service_amount_cents"}
FIXTURES = Path(__file__).parent / "fixtures"


class ReconciliationError(ValueError):
    pass


def digest(value) -> str:
    payload = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def timestamp(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value
    ):
        raise ReconciliationError("Timestamps require ISO seconds, an explicit offset and at most six fractional digits.")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ReconciliationError("Invalid source timestamp.") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ReconciliationError("Timestamps require an explicit UTC offset.")
    return instant.astimezone(timezone.utc).isoformat()


def empty_ledger() -> dict:
    return {"version": 1, "events": {}, "deliveries": {}, "last_export_as_of": None, "active": None}


def read_export(path: Path) -> dict:
    events = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != FIELDS or len(reader.fieldnames or []) != len(FIELDS):
            raise ReconciliationError("Export headers must match the documented payment contract.")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ReconciliationError("Malformed export row.")
            item = {key: value.strip() for key, value in row.items()}
            if any(not item[key] for key in ("payment_id", "booking_id", "customer_id")):
                raise ReconciliationError("Payment, booking and customer IDs are required.")
            if item["payment_id"] in events:
                raise ReconciliationError("Duplicate payment ID within one cumulative export.")
            if item["kind"] not in {"retainer", "balance", "refund"}:
                raise ReconciliationError("Unsupported payment kind.")
            if not re.fullmatch(r"[0-9]+", item["service_amount_cents"]):
                raise ReconciliationError("Amounts must be nonnegative integer service cents.")
            item["service_amount_cents"] = int(item["service_amount_cents"])
            item["occurred_at"] = timestamp(item["occurred_at"])
            events[item["payment_id"]] = item
    if not events:
        raise ReconciliationError("Empty exports do not establish a zero-revenue reset.")
    return events


def booking_summary(events: dict) -> dict:
    grouped = defaultdict(list)
    for event in events.values():
        grouped[event["booking_id"]].append(event)
    result = {}
    for booking_id, rows in sorted(grouped.items()):
        customers = {row["customer_id"] for row in rows}
        if len(customers) != 1:
            raise ReconciliationError("One booking maps to conflicting customer identities.")
        retainers = [row for row in rows if row["kind"] == "retainer"]
        charge = sum(row["service_amount_cents"] for row in rows if row["kind"] != "refund")
        refund = sum(row["service_amount_cents"] for row in rows if row["kind"] == "refund")
        result[booking_id] = {"booking_id": booking_id, "customer_id": next(iter(customers)),
                              "service_value_cents": charge, "refund_cents": refund,
                              "net_service_cash_cents": charge - refund,
                              "retainer_count": len(retainers),
                              "conversion_time": retainers[0]["occurred_at"] if len(retainers) == 1 else None,
                              "payment_events": len(rows)}
    return result


def nonblank_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_metadata(metadata: dict) -> dict:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("bookings", {}), dict):
        raise ReconciliationError("Metadata must contain a booking evidence mapping.")
    as_of = timestamp(metadata.get("export_as_of", ""))
    for booking, evidence in metadata.get("bookings", {}).items():
        if not booking or not isinstance(evidence, dict) or not nonblank_text(evidence.get("customer_id")):
            raise ReconciliationError("Malformed booking identity evidence.")
        consent = evidence.get("consent", "unknown")
        if consent not in {"granted", "denied", "unknown"}:
            raise ReconciliationError("Unsupported consent state.")
        if consent != "unknown" and not nonblank_text(evidence.get("consent_source")):
            raise ReconciliationError("A claimed consent state requires a source reference.")
        clicks = evidence.get("click_candidates", [])
        if not isinstance(clicks, list) or any(not nonblank_text(x) for x in clicks):
            raise ReconciliationError("Malformed synthetic click candidate list.")
        if len(set(clicks)) != len(clicks):
            raise ReconciliationError("Repeated click candidates are not independent evidence.")
        if clicks and not nonblank_text(evidence.get("click_source")):
            raise ReconciliationError("Click candidates require a source reference.")
    return {**metadata, "export_as_of": as_of}


def plan_export(export: Path, metadata_path: Path, ledger: dict) -> dict:
    if ledger.get("version") != 1:
        raise ReconciliationError("Unsupported ledger version.")
    if ledger.get("active") and ledger["active"]["status"] != "complete":
        raise ReconciliationError("Resume the active partial plan with apply before a new export. "
                                  "No command closes out a plan that cannot complete; that needs a separate manual decision.")
    events = read_export(export)
    metadata = validate_metadata(json.loads(metadata_path.read_text(encoding="utf-8")))
    as_of = metadata["export_as_of"]
    if ledger["last_export_as_of"] and as_of < ledger["last_export_as_of"]:
        raise ReconciliationError("Export watermark regressed.")
    if any(event["occurred_at"] > as_of for event in events.values()):
        raise ReconciliationError("A payment occurs after the declared export watermark.")
    previous = ledger["events"]
    if set(previous) - set(events):
        raise ReconciliationError("Cumulative export omitted previously recorded payment IDs.")
    if any(events[key] != value for key, value in previous.items()):
        raise ReconciliationError("A recorded payment changed; no command corrects source history, so planning stops.")
    bookings = booking_summary(events)
    actions, holds = [], {}
    evidence_by_booking = metadata.get("bookings", {})
    click_owners = defaultdict(set)
    # An accepted source claim survives a later sidecar that omits that claim.
    # This is a conservative demo policy, not an advertising platform invariant.
    for booking_id, accepted in ledger["deliveries"].items():
        if accepted.get("click_id"):
            click_owners[accepted["click_id"]].add(booking_id)
    for booking in bookings.values():
        evidence = evidence_by_booking.get(booking["booking_id"], {})
        if evidence and evidence.get("customer_id") != booking["customer_id"]:
            raise ReconciliationError("Booking evidence conflicts with the payment customer identity.")
        for click in evidence.get("click_candidates", []):
            click_owners[click].add(booking["booking_id"])
    for booking_id, booking in bookings.items():
        evidence = evidence_by_booking.get(booking_id, {})
        clicks = evidence.get("click_candidates", [])
        accepted = ledger["deliveries"].get(booking_id)
        reason = None
        if booking["retainer_count"] != 1:
            reason = "retainer timestamp is missing or ambiguous"
        elif booking["refund_cents"]:
            reason = "refund requires an explicit attribution adjustment decision"
        elif evidence.get("consent", "unknown") != "granted":
            reason = "consent is denied or lacks evidence"
        elif len(clicks) > 1:
            reason = "multiple click candidates are ambiguous"
        elif clicks and len(click_owners[clicks[0]]) > 1:
            reason = "one click is claimed by multiple bookings"
        elif accepted and clicks and clicks[0] != accepted.get("click_id"):
            reason = "accepted click evidence changed; explicit correction is required"
        if reason:
            holds[booking_id] = reason
            continue
        conversion_time = accepted["conversion_time"] if accepted else booking["conversion_time"]
        if accepted and accepted["value_cents"] == booking["service_value_cents"]:
            continue
        action = {"type": "restate" if accepted else "create", "booking_id": booking_id,
                  "customer_id": booking["customer_id"], "value_cents": booking["service_value_cents"],
                  "conversion_time": conversion_time,
                  "click_id": accepted.get("click_id") if accepted else (clicks[0] if clicks else None),
                  "click_source": accepted.get("click_source") if accepted else (evidence.get("click_source") if clicks else None),
                  "consent": "granted", "consent_source": evidence["consent_source"]}
        action["operation_id"] = digest(action)
        actions.append(action)
    return {"version": 1, "operation": "reconcile_local_fixture", "before": ledger,
            "input_hashes": {"export": digest(export.read_bytes()), "metadata": digest(metadata_path.read_bytes())},
            "export_as_of": as_of, "events": events, "bookings": bookings,
            "actions": actions, "holds": holds, "data_origin": "synthetic demonstration"}


def validate_plan(plan: dict, export: Path, metadata: Path) -> str:
    if not isinstance(plan, dict) or plan.get("operation") != "reconcile_local_fixture":
        raise ReconciliationError("Unsupported plan or execution target.")
    if plan != plan_export(export, metadata, plan["before"]):
        raise ReconciliationError("Plan or source bytes changed; obtain a new approval.")
    return digest(plan)


def approve(plan: dict, export: Path, metadata: Path, actor: str) -> dict:
    if not actor.strip():
        raise ReconciliationError("Approver label is required.")
    return {"plan_sha256": validate_plan(plan, export, metadata), "approver": actor,
            "authorization_type": "local-file attestation; not authenticated production identity"}
