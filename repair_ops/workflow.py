"""The graph returns a review and audit metadata, with no write capability."""

from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .fixtures import Identity, Order, Passage, lookup_order, retrieve
from .models import Provider, Recommendation, expected_outcome


class State(TypedDict, total=False):
    identity: Identity
    order_id: str
    order: Order | None
    passage: Passage | None
    policy: str
    result: dict
    attempts: list[dict]


def authorize(state: State) -> dict:
    order = lookup_order(state["identity"], state["order_id"])
    if order:
        return {"order": order, "policy": "allowed"}
    return {"policy": "denied", "result": {"outcome": "denied", "reason": "Order unavailable"}}


def search(state: State) -> dict:
    passage = retrieve(state["identity"], state["order"])
    if not passage or not passage.current:
        reason = "missing_procedure" if passage is None else "stale_procedure"
        return {"passage": passage, "result": {"outcome": "abstain", "reason": reason}}
    return {"passage": passage}


def build_graph(primary: Provider, fallback: Provider):
    def recommend(state: State) -> dict:
        order, passage = state["order"], state["passage"]
        attempts = []
        for provider in (primary, fallback):
            attempt = {"provider": provider.name, "status": "error"}
            attempts.append(attempt)
            try:
                raw, usage = provider.review(order, passage)
                if usage:
                    attempt["token_usage"] = usage
                recommendation = Recommendation.model_validate_json(raw)
                if (recommendation.citation != passage.id
                        or recommendation.outcome != expected_outcome(order, passage)):
                    raise ValueError("Ungrounded result")
            except Exception:
                # Provider payloads and exception strings can contain sensitive data.
                continue
            attempt["status"] = "ok"
            result = recommendation.model_dump()
            if recommendation.outcome == "draft_follow_up":
                result["draft"] = f"Request the {passage.required_attachment} for {order.id}."
            return {"result": result, "attempts": attempts}
        return {
            "result": {"outcome": "model_error", "reason": "Both provider attempts failed"},
            "attempts": attempts,
        }

    graph = StateGraph(State)
    graph.add_node("authorize", authorize)
    graph.add_node("retrieve", search)
    graph.add_node("recommend", recommend)
    graph.add_edge(START, "authorize")
    graph.add_conditional_edges("authorize", lambda state: state["policy"],
                                {"allowed": "retrieve", "denied": END})
    graph.add_conditional_edges("retrieve", lambda state: "result" not in state,
                                {True: "recommend", False: END})
    graph.add_edge("recommend", END)
    return graph.compile()


def evidence_view(passage: Passage | None) -> list[dict]:
    if passage is None:
        return []
    return [asdict(passage) | {"id": passage.id}]
