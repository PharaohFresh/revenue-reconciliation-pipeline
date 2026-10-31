from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .model import FIXTURES, ReconciliationError, approve, plan_export
from .workflow import execute, immutable_json, read_state


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def demo(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, faults in [
        ("01_deposits", {"booking-beta":{"mode":"permanent"}}),
        ("02_balances", {"booking-alpha":{"mode":"disconnect_after_accept"}}),
        ("03_refund", {}),
    ]:
        export=FIXTURES/(name+".csv")
        metadata=FIXTURES/(name+".json")
        plan=plan_export(export,metadata,read_state(root))
        approval=approve(plan,export,metadata,"synthetic-demo-reviewer")
        report=execute(plan,approval,export,metadata,root,faults=faults)
        reports.append(report)
        if report["status"]=="partial":
            # Retry the exact plan; the successful booking must not be submitted again.
            reports.append(execute(plan,approval,export,metadata,root))
        replay=plan_export(export,metadata,read_state(root))
        if replay["actions"]:
            raise ReconciliationError("Identical export produced repeated delivery actions.")
    summary={"data_origin":"synthetic", "runs":reports, "final_ledger":read_state(root)}
    immutable_json(root/"demo-report.json",summary)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description="Reconcile cumulative payments and deliver booking-grain updates to a local fake API.")
    subs=parser.add_subparsers(dest="command",required=True)
    d=subs.add_parser("demo")
    d.add_argument("--output",type=Path)
    p=subs.add_parser("plan")
    p.add_argument("--export",required=True,type=Path)
    p.add_argument("--metadata",required=True,type=Path)
    p.add_argument("--state-dir",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    for name in ["approve","apply"]:
        c=subs.add_parser(name)
        c.add_argument("--plan",required=True,type=Path)
        c.add_argument("--export",required=True,type=Path)
        c.add_argument("--metadata",required=True,type=Path)
        if name=="approve":
            c.add_argument("--actor",required=True)
            c.add_argument("--output",required=True,type=Path)
        else:
            c.add_argument("--approval",required=True,type=Path)
            c.add_argument("--state-dir",required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=="demo":
            output=args.output or Path("artifacts")/("demo-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            result=demo(output)
            for index,run in enumerate(result["runs"],1):
                print(f"[RUN {index}] {run['status']}: {run['completed_operations']}/{run['planned_operations']} operations; {len(run['holds'])} holds")
            print("[OK] Cumulative exports, partial-failure resume, replay and refund hold reproduced.")
            print("Artifacts: "+str(output))
        elif args.command=="plan":
            result=plan_export(args.export,args.metadata,read_state(args.state_dir))
            immutable_json(args.output,result)
            print("[OK] Plan written; no payment or delivery state changed.")
        elif args.command=="approve":
            immutable_json(args.output,approve(load(args.plan),args.export,args.metadata,args.actor))
            print("[OK] Local approval attestation binds the exact plan and source bytes.")
        else:
            result=execute(load(args.plan),load(args.approval),args.export,args.metadata,args.state_dir)
            print(json.dumps(result,indent=2))
            return 0 if result["status"]=="complete" else 1
    except (ReconciliationError,OSError,ValueError,KeyError) as exc:
        print("[BLOCKED] "+str(exc))
        return 1
    return 0
