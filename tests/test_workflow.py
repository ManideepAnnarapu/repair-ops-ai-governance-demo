import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from repair_ops.app import connect, create_app
from repair_ops.cli import headers
from repair_ops.models import LocalProvider


@pytest.fixture
def system(tmp_path):
    path = tmp_path / "review.sqlite3"
    with TestClient(create_app(path)) as client:
        yield client, path


def review(client, order="RO-101", user="cedar-writer"):
    return client.post(f"/reviews/{order}", headers=headers(user))


def trace(client, body, user="cedar-writer"):
    return client.get(f"/reviews/{body['request_id']}/trace", headers=headers(user))


def decide(client, body, action="approve", user="regional-reviewer"):
    return client.post(f"/reviews/{body['request_id']}/decision", headers=headers(user),
                       json={"decision": action})


def task_count(path):
    with connect(path) as db:
        return db.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0]


def test_cited_review_and_current_version(system):
    client, _ = system
    response = review(client)
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "draft_follow_up"
    assert len(body["evidence"]) == 1
    source = body["evidence"][0]
    assert body["citation"] == source["id"] == "CED-CAMERA@2"
    assert (source["document_id"], source["version"]) == ("CED-CAMERA", 2)
    assert source["text"] in body["rationale"]
    complete = review(client, "RO-102").json()
    assert complete["outcome"] == "complete"
    assert "draft" not in complete


def test_order_procedure_and_trace_are_shop_scoped(system):
    client, _ = system
    assert client.post("/reviews/RO-101").status_code == 401
    denied = review(client, "RO-201")
    assert denied.status_code == 404
    body = denied.json()
    assert body["evidence"] == []
    assert "verification sheet" not in denied.text
    assert trace(client, body).json()["policy_decision"] == "denied"
    assert trace(client, body, "flint-writer").status_code == 404

    regional = review(client, "RO-201", "regional-reviewer").json()
    assert [source["id"] for source in regional["evidence"]] == ["FLT-CAMERA@1"]
    assert trace(client, regional).status_code == 404


@pytest.mark.parametrize(("order", "reason", "ids"), [
    ("RO-103", "stale_procedure", ["CED-ALIGN@1"]),
    ("RO-104", "missing_procedure", []),
])
def test_abstention_skips_providers(system, order, reason, ids):
    client, path = system
    body = review(client, order).json()
    assert (body["outcome"], body["reason"]) == ("abstain", reason)
    assert "draft" not in body
    audit = trace(client, body).json()
    assert audit["provider_attempts"] == []
    assert audit["evidence_ids"] == ids
    assert decide(client, body).status_code == 409
    assert task_count(path) == 0


@pytest.mark.parametrize("fallback_fails", [False, True])
def test_provider_failure_and_fallback(tmp_path, fallback_fails):
    path = tmp_path / "fallback.sqlite3"
    with TestClient(create_app(path, LocalProvider("primary", True),
                               LocalProvider("fallback", fallback_fails))) as client:
        response = review(client)
        body = response.json()
        audit = trace(client, body).json()
        assert audit["fallback_attempted"]
        assert audit["provider_attempts"] == [
            {"provider": "primary", "status": "error"},
            {"provider": "fallback", "status": "error" if fallback_fails else "ok"},
        ]
        if fallback_fails:
            assert response.status_code == 503
            assert body["outcome"] == "model_error"
            assert body["reason"] == "Both provider attempts failed"
            assert "draft" not in body
            assert decide(client, body).status_code == 409
        else:
            assert response.status_code == 200
            assert body["outcome"] == "draft_follow_up"
        assert task_count(path) == 0


@pytest.mark.parametrize("raw", [
    "invalid JSON",
    '{"citation":"FLT-CAMERA@1","outcome":"complete","rationale":"wrong source"}',
    '{"citation":"CED-CAMERA@2","outcome":"complete","rationale":"wrong outcome"}',
])
def test_invalid_model_output_falls_back(tmp_path, raw):
    class InvalidProvider:
        name = "invalid"

        def review(self, order, passage):
            return raw, {"inputTokens": 12, "outputTokens": 3}

    with TestClient(create_app(tmp_path / "invalid.sqlite3", InvalidProvider())) as client:
        body = review(client).json()
        assert body["citation"] == "CED-CAMERA@2"
        assert body["outcome"] == "draft_follow_up"
        attempts = trace(client, body).json()["provider_attempts"]
        assert attempts[0]["token_usage"] == {"inputTokens": 12, "outputTokens": 3}
        assert attempts[1] == {"provider": "local-fallback", "status": "ok"}


def test_authorized_approval_is_idempotent_under_concurrency(system):
    client, path = system
    draft = review(client).json()
    assert task_count(path) == 0
    assert trace(client, draft).json()["approval_outcome"] == "pending"
    assert decide(client, draft, user="cedar-writer").status_code == 403
    with ThreadPoolExecutor(max_workers=4) as executor:
        attempts = list(executor.map(lambda _: decide(client, draft), range(4)))
    assert all(attempt.status_code == 200 for attempt in attempts)
    created = attempts[0].json()
    assert created["approval_outcome"] == "approved"
    assert created["follow_up_id"] is not None
    assert all(attempt.json() == created for attempt in attempts)
    assert task_count(path) == 1
    audit = trace(client, draft).json()
    assert audit["approval_outcome"] == "approved"
    assert audit["decided_by"] == "regional-reviewer"


def test_rejection_is_terminal_and_creates_no_task(system):
    client, path = system
    draft = review(client).json()
    assert decide(client, draft, "reject").json() == {
        "approval_outcome": "rejected", "follow_up_id": None,
    }
    assert decide(client, draft).status_code == 409
    assert task_count(path) == 0
    assert trace(client, draft).json()["approval_outcome"] == "rejected"


def test_trace_has_only_allowlisted_metadata(system):
    client, _ = system
    body = review(client).json()
    audit = trace(client, body).json()
    assert audit == {
        "request_id": body["request_id"],
        "policy_decision": "allowed",
        "outcome": "draft_follow_up",
        "evidence_ids": ["CED-CAMERA@2"],
        "prompt_version": "repair-review-v1",
        "provider_attempts": [{"provider": "local", "status": "ok"}],
        "fallback_attempted": False,
        "latency_ms": audit["latency_ms"],
        "approval_outcome": "pending",
    }
    assert audit["latency_ms"] >= 0
    assert "RO-101" not in json.dumps(audit)
    assert body["evidence"][0]["text"] not in json.dumps(audit)
