"""HTTP review, audit and human approval endpoints."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict

from .fixtures import IDENTITIES, Identity
from .models import PROMPT_VERSION, BedrockProvider, LocalProvider, Provider
from .workflow import build_graph, evidence_view


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def demo_identity(x_demo_user: Annotated[str | None, Header()] = None) -> tuple[str, Identity]:
    identity = IDENTITIES.get(x_demo_user)
    if identity is None:
        raise HTTPException(401, "Select a demo identity with X-Demo-User")
    return x_demo_user, identity


DemoActor = Annotated[tuple[str, Identity], Depends(demo_identity)]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


def create_app(
    path: Path,
    primary: Provider | None = None,
    fallback: Provider | None = None,
) -> FastAPI:
    graph = build_graph(primary or LocalProvider(), fallback or LocalProvider("local-fallback"))
    api = FastAPI(title="Repair Ops AI Governance Demo")
    with connect(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                shop TEXT,
                demo_actor TEXT NOT NULL,
                trace TEXT NOT NULL,
                draft TEXT,
                decision TEXT NOT NULL,
                decided_by TEXT
            );
            CREATE TABLE IF NOT EXISTS follow_ups (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                description TEXT NOT NULL
            );
        """)

    def authorized_run(db, request_id, actor):
        demo_actor, identity = actor
        row = db.execute("SELECT * FROM runs WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Run unavailable")
        if row["shop"] is None:
            allowed = row["demo_actor"] == demo_actor or identity.reviewer
        else:
            allowed = row["shop"] in identity.shops
        if not allowed:
            raise HTTPException(404, "Run unavailable")
        return row

    @api.post("/reviews/{order_id}")
    def review(order_id: str, response: Response, actor: DemoActor):
        demo_actor, identity = actor
        started = perf_counter()
        request_id = str(uuid4())
        with tracing_context(enabled=False):
            state = graph.invoke({"identity": identity, "order_id": order_id})
        evidence = evidence_view(state.get("passage"))
        result = state["result"]
        attempts = state.get("attempts", [])
        decision = "pending" if "draft" in result else "not_applicable"
        trace = {
            "request_id": request_id,
            "policy_decision": state["policy"],
            "outcome": result["outcome"],
            "evidence_ids": [passage["id"] for passage in evidence],
            "prompt_version": PROMPT_VERSION,
            "provider_attempts": attempts,
            "fallback_attempted": len(attempts) > 1,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }
        order = state.get("order")
        with connect(path) as db:
            db.execute(
                "INSERT INTO runs (id, shop, demo_actor, trace, draft, decision) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, order.shop if order else None, demo_actor, json.dumps(trace),
                 result.get("draft"), decision),
            )
        response.status_code = {"denied": 404, "model_error": 503}.get(result["outcome"], 200)
        return {"request_id": request_id, "evidence": evidence, **result}

    @api.get("/reviews/{request_id}/trace")
    def trace(request_id: str, actor: DemoActor):
        with connect(path) as db:
            row = authorized_run(db, request_id, actor)
            audit = json.loads(row["trace"]) | {"approval_outcome": row["decision"]}
            if row["decided_by"]:
                audit["decided_by"] = row["decided_by"]
            return audit

    @api.post("/reviews/{request_id}/decision")
    def decide(request_id: str, action: Decision, actor: DemoActor):
        demo_actor, identity = actor
        if not identity.reviewer:
            raise HTTPException(403, "Reviewer role required")
        target = "approved" if action.decision == "approve" else "rejected"
        with connect(path) as db:
            # Serialize the check, state transition and insert across concurrent reviewers.
            db.execute("BEGIN IMMEDIATE")
            row = authorized_run(db, request_id, actor)
            if row["draft"] is None or row["decision"] not in ("pending", target):
                raise HTTPException(409, "Decision conflicts with run state")
            if row["decision"] == "pending":
                db.execute("UPDATE runs SET decision = ?, decided_by = ? WHERE id = ?",
                           (target, demo_actor, request_id))
                if target == "approved":
                    db.execute("INSERT INTO follow_ups (run_id, description) VALUES (?, ?)",
                               (request_id, row["draft"]))
            task = db.execute("SELECT id FROM follow_ups WHERE run_id = ?", (request_id,)).fetchone()
        return {"approval_outcome": target, "follow_up_id": task["id"] if task else None}

    return api


def app_from_env() -> FastAPI:
    model_id = os.getenv("REPAIR_BEDROCK_MODEL_ID")
    primary = BedrockProvider(model_id) if model_id else LocalProvider()
    return create_app(Path(os.getenv("REPAIR_DB", "repair-ops.sqlite3")), primary)
