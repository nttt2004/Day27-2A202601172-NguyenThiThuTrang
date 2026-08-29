"""Phan 5 - Kiem tra ket qua (chay khong can Streamlit).

    python run_checks.py

Kiem tra: state, agent reasoning, 3 routing rule, interrupt, resume
(approve / reject / edit) va audit log append-only.
"""

from __future__ import annotations

from uuid import uuid4

from graph import (
    CONFIDENCE_THRESHOLD,
    build_graph,
    evaluate_customer,
    initial_state,
    route_action,
)
from models import load_audit_log

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {detail}" if detail else ""))


def run_until_pending(graph, customer_id: str):
    """Chay graph toi khi ket thuc hoac dung truoc high-risk action."""
    config = {"configurable": {"thread_id": f"check-{customer_id}-{uuid4().hex[:6]}"}}
    graph.invoke(initial_state(customer_id), config)
    return config, graph.get_state(config)


def main() -> int:
    graph = build_graph()

    print("\n=== 1. GraphState + agent reasoning ===")
    state = initial_state("CUST001")
    out = evaluate_customer(state)
    state.update(out)
    for key in ("customer_id", "proposed_action", "confidence_score", "reasoning", "human_decision"):
        check(f"state co key '{key}'", key in state)
    check(
        "0.0 <= confidence_score <= 1.0",
        0.0 <= state["confidence_score"] <= 1.0,
        str(state["confidence_score"]),
    )
    check("reasoning khong rong", bool(state["reasoning"].strip()))

    print("\n=== 2. Routing rules ===")
    hard = {"proposed_action": "increase_credit_limit", "confidence_score": 0.99}
    check(
        "Rule 1 - increase_credit_limit @0.99 -> human review",
        route_action(hard) == "execute_high_risk_action",
        route_action(hard),
    )
    auto = {"proposed_action": "send_email", "confidence_score": 0.90}
    check(
        "Rule 2 - send_email @0.90 -> auto execute",
        route_action(auto) == "execute_low_risk_action",
        route_action(auto),
    )
    esc = {"proposed_action": "send_email", "confidence_score": 0.82}
    check(
        f"Rule 3 - send_email @0.82 (< {CONFIDENCE_THRESHOLD}) -> human review",
        route_action(esc) == "execute_high_risk_action",
        route_action(esc),
    )

    print("\n=== 3. Interrupt truoc high-risk action ===")
    config, snap = run_until_pending(graph, "CUST004")
    check("graph dung o pending state", snap.next == ("execute_high_risk_action",), str(snap.next))
    check("high-risk action CHUA chay", not snap.values.get("executed"))
    check("state van giu customer data", snap.values.get("customer_data", {}).get("toi") == 1_250_000_000)
    check(
        "confidence 0.99 van khong bypass duoc policy",
        snap.values["confidence_score"] >= 0.99
        and snap.values["proposed_action"] == "increase_credit_limit",
        f"conf={snap.values['confidence_score']}",
    )

    audit_before = len(load_audit_log())

    print("\n=== 4. Resume - Approve ===")
    graph.update_state(config, {"human_decision": "approve"})
    final = graph.invoke(None, config)
    check("approve -> action duoc thuc thi", final["executed"] is True, final["execution_result"])

    print("\n=== 5. Resume - Reject ===")
    config, snap = run_until_pending(graph, "CUST001")
    graph.update_state(config, {"human_decision": "reject"})
    final = graph.invoke(None, config)
    check("reject -> action bi huy", final["executed"] is False, final["execution_result"])

    print("\n=== 6. Resume - Edit ===")
    config, snap = run_until_pending(graph, "CUST001")
    original_amount = snap.values["action_params"]["amount"]
    graph.update_state(
        config,
        {
            "human_decision": "edit",
            "action_params": {"amount": 20_000_000, "currency": "VND"},
        },
    )
    final = graph.invoke(None, config)
    check(
        "edit -> chay voi params da sua",
        final["executed"] is True and final["action_params"]["amount"] == 20_000_000,
        f"{original_amount:,} -> {final['action_params']['amount']:,}",
    )

    print("\n=== 7. Auto-execute (low risk) ===")
    config, snap = run_until_pending(graph, "CUST002")
    check(
        "CUST002 auto execute, khong can human",
        snap.next == () and snap.values["executed"] is True,
        snap.values["execution_result"],
    )

    print("\n=== 8. Escalate vi confidence thap ===")
    config, snap = run_until_pending(graph, "CUST003")
    check(
        f"CUST003 (send_email @{snap.values['confidence_score']}) -> human review",
        snap.next == ("execute_high_risk_action",),
        str(snap.next),
    )
    graph.update_state(config, {"human_decision": "approve"})
    graph.invoke(None, config)

    print("\n=== 9. Audit trail ===")
    entries = load_audit_log()
    check(
        "audit log duoc append them entry moi",
        len(entries) > audit_before,
        f"{audit_before} -> {len(entries)}",
    )
    decisions = {e["decision"] for e in entries}
    for want in ("approve", "reject", "edit", "auto_execute"):
        check(f"audit co decision '{want}'", want in decisions)
    required = {"timestamp", "agent_id", "action", "confidence", "reviewer_id", "decision"}
    check("moi entry co du 6 field bat buoc", all(required <= set(e) for e in entries))

    print(f"\n==> PASSED {len(PASSED)} / FAILED {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
