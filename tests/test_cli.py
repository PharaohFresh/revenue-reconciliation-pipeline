import json

from revenue_reconciliation.cli import main


def test_full_demo(tmp_path):
    root=tmp_path/"demo"
    assert main(["demo","--output",str(root)])==0
    report=json.loads((root/"demo-report.json").read_text())
    assert len(report["runs"])==4
    assert report["runs"][0]["status"]=="partial"
    assert report["runs"][-1]["status"]=="complete"
    assert report["runs"][-1]["holds"]
    assert main(["demo","--output",str(root)])==1
