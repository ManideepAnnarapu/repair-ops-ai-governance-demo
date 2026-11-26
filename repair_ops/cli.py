"""Run the offline demo or evaluate the keyword workflow."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from .app import create_app
from .models import LocalProvider


def headers(actor="cedar-writer"):
    return {"X-Demo-User": actor}


def show(label, response):
    print(f"\n{label} (HTTP {response.status_code})")
    print(json.dumps(response.json(), indent=2))
    return response.json()


def demo(directory):
    with TestClient(create_app(directory / "demo.sqlite3")) as client:
        review = show("Cedar Arc review", client.post("/reviews/RO-101", headers=headers()))
        denied = show("Cross-shop denial", client.post("/reviews/RO-201", headers=headers()))
        show("Denial trace", client.get(f"/reviews/{denied['request_id']}/trace", headers=headers()))
        run = f"/reviews/{review['request_id']}"
        for label in ("Human approval", "Retried approval"):
            show(label, client.post(f"{run}/decision", headers=headers("regional-reviewer"),
                                    json={"decision": "approve"}))
        show("Approved run trace", client.get(f"{run}/trace", headers=headers()))

    primary = LocalProvider("simulated-primary", fail=True)
    with TestClient(create_app(directory / "fallback.sqlite3", primary)) as client:
        result = show("Fallback review", client.post("/reviews/RO-101", headers=headers()))
        show("Fallback trace", client.get(f"/reviews/{result['request_id']}/trace", headers=headers()))


def evaluate(directory, dataset):
    cases = json.loads(dataset.read_text())
    passed = 0
    with TestClient(create_app(directory / "eval.sqlite3")) as client:
        for case in cases:
            response = client.post(f"/reviews/{case['order']}", headers=headers(case["actor"]))
            body = response.json()
            actual = {
                "status": response.status_code,
                "evidence_ids": [item["id"] for item in body["evidence"]],
                "outcome": body["outcome"],
                "reason": body.get("reason"),
            }
            match = actual == ({"reason": None} | case["expected"])
            passed += match
            print(f"{case['name']} {'PASS' if match else f'FAIL {actual}'}")
    print(f"{passed}/{len(cases)} deterministic keyword/workflow cases passed")
    return int(not cases or passed != len(cases))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "eval"])
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/cases.json"))
    options = parser.parse_args()
    with TemporaryDirectory() as directory:
        if options.command == "eval":
            return evaluate(Path(directory), options.dataset)
        demo(Path(directory))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
