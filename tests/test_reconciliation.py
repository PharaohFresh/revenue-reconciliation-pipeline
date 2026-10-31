import copy
import csv
import json
from pathlib import Path

import pytest

from revenue_reconciliation.model import (
    FIXTURES, ReconciliationError, approve, booking_summary, digest,
    empty_ledger, plan_export, read_export, timestamp,
)
from revenue_reconciliation.platform import FakePlatform, atomic_json
from revenue_reconciliation.workflow import execute, read_state


def inputs(name):
    return FIXTURES/(name+".csv"), FIXTURES/(name+".json")


def deliver(name, root, **kwargs):
    export,metadata=inputs(name)
    plan=plan_export(export,metadata,read_state(root))
    return execute(plan,approve(plan,export,metadata,"test-reviewer"),export,metadata,root,**kwargs)


def altered_inputs(tmp_path, name="01_deposits", *, rows=None, metadata=None):
    export,meta=inputs(name)
    destination=tmp_path/"export.csv";md=tmp_path/"metadata.json"
    if rows is None:destination.write_bytes(export.read_bytes())
    else:
        with destination.open("w",newline="",encoding="utf-8") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    md.write_text(json.dumps(metadata if metadata is not None else json.loads(meta.read_text())))
    return destination,md


def source_rows(name="01_deposits"):
    with inputs(name)[0].open(newline="") as handle:return list(csv.DictReader(handle))


def test_three_exports_preserve_payment_and_booking_grains(tmp_path):
    root=tmp_path/"state"
    deliver("01_deposits",root)
    before=read_state(root)["deliveries"]["booking-alpha"]["conversion_time"]
    second=deliver("02_balances",root)
    assert second["payment_event_count"]==5 and second["booking_count"]==3
    assert second["source_service_value_cents"]==154500
    assert second["accepted_conversion_value_cents"]==134500
    assert "booking-gamma" in second["holds"]
    final=deliver("03_refund",root)
    assert final["payment_event_count"]==7 and final["booking_count"]==4
    assert final["source_service_value_cents"]==172500
    assert final["source_net_service_cash_cents"]==162500
    assert final["accepted_conversion_value_cents"]==152500
    assert read_state(root)["deliveries"]["booking-alpha"]["conversion_time"]==before
    assert "booking-alpha" in final["holds"]
    assert "booking-delta" in read_state(root)["deliveries"]
    assert final["matching_proven"] is False and final["reported_attribution_proven"] is False


def test_identical_cumulative_export_does_not_double_count_or_repeat_requests(tmp_path):
    root=tmp_path/"state";deliver("01_deposits",root)
    export,metadata=inputs("01_deposits")
    plan=plan_export(export,metadata,read_state(root))
    assert plan["actions"]==[] and len(plan["events"])==2
    assert FakePlatform(root/"fake-platform.json").state()["unique_effects"]==2


def test_partial_failure_resumes_only_pending_operations(tmp_path):
    root=tmp_path/"state";export,metadata=inputs("01_deposits")
    plan=plan_export(export,metadata,empty_ledger());approval=approve(plan,export,metadata,"test")
    result=execute(plan,approval,export,metadata,root,faults={"booking-beta":{"mode":"permanent"}})
    assert result["status"]=="partial" and result["completed_operations"]==1
    alpha_op=next(x["operation_id"] for x in plan["actions"] if x["booking_id"]=="booking-alpha")
    result=execute(plan,approval,export,metadata,root)
    assert result["status"]=="complete"
    platform=FakePlatform(root/"fake-platform.json").state()
    assert platform["attempts"][alpha_op]==1 and platform["unique_effects"]==2


def test_transient_retries_are_bounded(tmp_path):
    root=tmp_path/"state";export,metadata=inputs("01_deposits")
    plan=plan_export(export,metadata,empty_ledger());approval=approve(plan,export,metadata,"test")
    result=execute(plan,approval,export,metadata,root,faults={"booking-beta":{"mode":"transient","failures":20}},max_attempts=3)
    beta_op=next(x["operation_id"] for x in plan["actions"] if x["booking_id"]=="booking-beta")
    assert result["status"]=="partial"
    assert FakePlatform(root/"fake-platform.json").state()["attempts"][beta_op]==3
    with pytest.raises(ReconciliationError,match="active partial"):
        plan_export(*inputs("02_balances"),read_state(root))


@pytest.mark.parametrize("name",["01_deposits","02_balances"])
def test_lost_response_recovers_create_or_restatement_without_duplicate_effect(tmp_path,name):
    root=tmp_path/"state"
    if name=="02_balances":deliver("01_deposits",root)
    export,metadata=inputs(name);plan=plan_export(export,metadata,read_state(root));approval=approve(plan,export,metadata,"test")
    first=execute(plan,approval,export,metadata,root,faults={"booking-alpha":{"mode":"disconnect_after_accept"}},max_attempts=1)
    assert first["status"]=="partial"
    effects=FakePlatform(root/"fake-platform.json").state()["unique_effects"]
    final=execute(plan,approval,export,metadata,root)
    assert final["status"]=="complete"
    assert FakePlatform(root/"fake-platform.json").state()["unique_effects"]==effects


def test_accepted_reply_with_wrong_readback_is_not_committed(tmp_path):
    root=tmp_path/"state"
    result=deliver("01_deposits",root,faults={"booking-beta":{"mode":"corrupt_readback"}})
    assert result["status"]=="partial" and "booking-beta" not in read_state(root)["deliveries"]
    assert any("readback" in text for text in result["failures"].values())


def test_stale_or_modified_approval_cannot_mutate_state(tmp_path):
    export,metadata=inputs("01_deposits");plan=plan_export(export,metadata,empty_ledger())
    root=tmp_path/"state"
    with pytest.raises(ReconciliationError):execute(plan,{},export,metadata,root)
    modified=copy.deepcopy(plan);modified["actions"][0]["value_cents"]+=1
    with pytest.raises(ReconciliationError):execute(modified,approve(plan,export,metadata,"test"),export,metadata,root)
    assert not root.exists()


def test_source_bytes_changed_after_approval_are_rejected(tmp_path):
    export,metadata=altered_inputs(tmp_path)
    plan=plan_export(export,metadata,empty_ledger());approval=approve(plan,export,metadata,"test")
    export.write_text(export.read_text()+"\n")
    with pytest.raises(ReconciliationError):execute(plan,approval,export,metadata,tmp_path/"state")


def test_ledger_drift_after_checkpoint_is_rejected(tmp_path):
    root=tmp_path/"state";export,metadata=inputs("01_deposits")
    plan=plan_export(export,metadata,empty_ledger());approval=approve(plan,export,metadata,"test")
    execute(plan,approval,export,metadata,root,faults={"booking-beta":{"mode":"permanent"}})
    state=read_state(root);state["events"]["payment-a1"]["service_amount_cents"]+=1;atomic_json(root/"ledger.json",state)
    with pytest.raises(ReconciliationError,match="drifted"):execute(plan,approval,export,metadata,root)


@pytest.mark.parametrize("mutation",["changed","omitted","duplicate","negative","fractional","naive_time","future","customer_conflict"])
def test_invalid_cumulative_input_is_rejected(tmp_path,mutation):
    root=tmp_path/"state";deliver("01_deposits",root);rows=source_rows()
    if mutation=="changed":rows[0]["service_amount_cents"]="25001"
    elif mutation=="omitted":rows=rows[1:]
    elif mutation=="duplicate":rows.append(dict(rows[0]))
    elif mutation=="negative":rows[0]["service_amount_cents"]="-1"
    elif mutation=="fractional":rows[0]["service_amount_cents"]="25000.5"
    elif mutation=="naive_time":rows[0]["occurred_at"]="2026-09-01T09:00:00"
    elif mutation=="future":rows[0]["occurred_at"]="2027-01-01T09:00:00+00:00"
    else:rows.append({**rows[0],"payment_id":"new","customer_id":"different"})
    export,metadata=altered_inputs(tmp_path,rows=rows)
    with pytest.raises(ReconciliationError):plan_export(export,metadata,read_state(root))


def test_watermark_regression_is_rejected(tmp_path):
    root=tmp_path/"state";deliver("01_deposits",root)
    metadata=json.loads(inputs("01_deposits")[1].read_text());metadata["export_as_of"]="2026-09-02T00:00:00+00:00"
    export,md=altered_inputs(tmp_path,metadata=metadata)
    with pytest.raises(ReconciliationError,match="watermark"):plan_export(export,md,read_state(root))


@pytest.mark.parametrize("state",["denied","unknown"])
def test_consent_is_not_inferred(tmp_path,state):
    metadata=json.loads(inputs("01_deposits")[1].read_text());metadata["bookings"]["booking-alpha"]["consent"]=state
    export,md=altered_inputs(tmp_path,metadata=metadata);plan=plan_export(export,md,empty_ledger())
    assert "booking-alpha" in plan["holds"] and len(plan["actions"])==1


def test_claimed_consent_requires_source_and_identity_match(tmp_path):
    metadata=json.loads(inputs("01_deposits")[1].read_text());del metadata["bookings"]["booking-alpha"]["consent_source"]
    export,md=altered_inputs(tmp_path,metadata=metadata)
    with pytest.raises(ReconciliationError):plan_export(export,md,empty_ledger())
    metadata=json.loads(inputs("01_deposits")[1].read_text());metadata["bookings"]["booking-alpha"]["customer_id"]="other"
    export,md=altered_inputs(tmp_path,metadata=metadata)
    with pytest.raises(ReconciliationError):plan_export(export,md,empty_ledger())


REFERENCE_FIELDS = [
    ("customer_id", "identity evidence"),
    ("consent_source", "consent state requires a source"),
    ("click_source", "Click candidates require a source"),
]


@pytest.mark.parametrize("bad", ["   ", {"not": "a string"}, 1])
@pytest.mark.parametrize("field,message", REFERENCE_FIELDS)
def test_identity_and_source_references_must_be_nonblank_strings(tmp_path, field, message, bad):
    metadata = json.loads(inputs("01_deposits")[1].read_text())
    metadata["bookings"]["booking-alpha"].update(click_candidates=["fake-click-a"], click_source="synthetic-event")
    export, md = altered_inputs(tmp_path, metadata=metadata)
    plan_export(export, md, empty_ledger())
    metadata["bookings"]["booking-alpha"][field] = bad
    export, md = altered_inputs(tmp_path, metadata=metadata)
    with pytest.raises(ReconciliationError, match=message):
        plan_export(export, md, empty_ledger())


def test_blank_click_candidate_is_rejected(tmp_path):
    metadata = json.loads(inputs("01_deposits")[1].read_text())
    metadata["bookings"]["booking-alpha"].update(click_candidates=["   "], click_source="synthetic-event")
    export, md = altered_inputs(tmp_path, metadata=metadata)
    with pytest.raises(ReconciliationError, match="click candidate list"):
        plan_export(export, md, empty_ledger())


def test_ambiguous_and_cross_booking_clicks_are_held(tmp_path):
    metadata=json.loads(inputs("01_deposits")[1].read_text())
    metadata["bookings"]["booking-alpha"].update(click_candidates=["fake-click-a","fake-click-b"],click_source="synthetic-event")
    export,md=altered_inputs(tmp_path,metadata=metadata);plan=plan_export(export,md,empty_ledger())
    assert "ambiguous" in plan["holds"]["booking-alpha"]
    for row in metadata["bookings"].values():row.update(click_candidates=["fake-shared-click"],click_source="synthetic-event")
    export,md=altered_inputs(tmp_path,metadata=metadata);plan=plan_export(export,md,empty_ledger())
    assert len(plan["holds"])==2 and plan["actions"]==[]


def test_missing_or_multiple_retainers_do_not_invent_conversion_time(tmp_path):
    rows=source_rows();rows[0]["kind"]="balance"
    export,md=altered_inputs(tmp_path,rows=rows);plan=plan_export(export,md,empty_ledger())
    assert "booking-alpha" in plan["holds"]
    rows=source_rows();rows.append({**rows[0],"payment_id":"other-deposit"})
    export,md=altered_inputs(tmp_path,rows=rows);plan=plan_export(export,md,empty_ledger())
    assert "booking-alpha" in plan["holds"]


def test_equivalent_offsets_are_normalized_to_same_instant():
    assert timestamp("2026-09-01T09:00:00-05:00")==timestamp("2026-09-01T14:00:00Z")


@pytest.mark.parametrize("value", ["2026-09-01T09:15:00.0000001-05:00",
 "2026-09-01T09:15:00.0000002-05:00", "2026-09-01T09:15:00+00:00:00.1"])
def test_unsupported_timestamp_precision_is_rejected(value):
    with pytest.raises(ReconciliationError, match="fractional digits"):
        timestamp(value)


@pytest.mark.parametrize("value", ["2026-09-01T09:15:00+00:60", "2026-09-01T09:15:00+01:99"])
def test_invalid_iso_offset_minutes_are_rejected(value):
    with pytest.raises(ReconciliationError):
        timestamp(value)


def test_supported_microsecond_history_change_is_not_lost(tmp_path):
    rows=source_rows();rows[0]["occurred_at"]="2026-09-01T09:15:00.000001-05:00"
    export,metadata=altered_inputs(tmp_path,rows=rows);root=tmp_path/"state"
    plan=plan_export(export,metadata,empty_ledger())
    execute(plan,approve(plan,export,metadata,"test"),export,metadata,root)
    rows[0]["occurred_at"]="2026-09-01T09:15:00.000002-05:00"
    export,metadata=altered_inputs(tmp_path,rows=rows)
    with pytest.raises(ReconciliationError, match="recorded payment changed"):
        plan_export(export,metadata,read_state(root))
    rows[0]["occurred_at"]="2026-09-01T09:15:00.0000001-05:00"
    export,metadata=altered_inputs(tmp_path,rows=rows)
    with pytest.raises(ReconciliationError, match="fractional digits"):
        plan_export(export,metadata,read_state(root))


def test_accepted_click_ownership_survives_omitted_sidecar_evidence(tmp_path):
    metadata=json.loads(inputs("01_deposits")[1].read_text())
    metadata["bookings"]["booking-alpha"].update(click_candidates=["fake-shared"],click_source="synthetic-event")
    export,md=altered_inputs(tmp_path,metadata=metadata);root=tmp_path/"state"
    plan=plan_export(export,md,empty_ledger())
    execute(plan,approve(plan,export,md,"test"),export,md,root)
    metadata=json.loads(inputs("02_balances")[1].read_text())
    metadata["bookings"]["booking-alpha"]["click_candidates"]=[]
    metadata["bookings"]["booking-gamma"].update(consent="granted",consent_source="synthetic-consent",
                                                 click_candidates=["fake-shared"],click_source="synthetic-event")
    export,md=altered_inputs(tmp_path,"02_balances",metadata=metadata)
    plan=plan_export(export,md,read_state(root))
    assert "multiple bookings" in plan["holds"]["booking-gamma"]
    assert not any(x["booking_id"]=="booking-gamma" for x in plan["actions"])
    alpha=next(x for x in plan["actions"] if x["booking_id"]=="booking-alpha")
    assert alpha["click_id"]=="fake-shared" and alpha["click_source"]=="synthetic-event"
    execute(plan,approve(plan,export,md,"test"),export,md,root)
    assert read_state(root)["deliveries"]["booking-alpha"]["click_id"]=="fake-shared"


def test_restatement_cannot_replace_accepted_attribution_evidence(tmp_path):
    root=tmp_path/"state";deliver("01_deposits",root)
    metadata=json.loads(inputs("02_balances")[1].read_text())
    metadata["bookings"]["booking-alpha"].update(click_candidates=["fake-new"],click_source="synthetic-new-event")
    export,md=altered_inputs(tmp_path,"02_balances",metadata=metadata)
    plan=plan_export(export,md,read_state(root))
    assert "explicit correction" in plan["holds"]["booking-alpha"]
    assert not any(x["booking_id"]=="booking-alpha" for x in plan["actions"])
