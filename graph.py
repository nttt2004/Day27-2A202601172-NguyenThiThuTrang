"""Day 27 - LangGraph workflow danh gia churn risk voi Human-in-the-Loop.

Luong:
    customer data -> evaluate_customer -> route_action
                                          |-> execute_low_risk_action  (auto)
                                          |-> execute_high_risk_action (interrupt -> human)
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from models import append_audit_entry, make_audit_entry

AGENT_ID = "churn-risk-agent"

# --- Policy configuration -------------------------------------------------
CONFIDENCE_THRESHOLD = 0.85                     # Rule 2: auto-execute khi >= nguong nay
HIGH_RISK_ACTIONS = {"increase_credit_limit"}   # Rule 1: hard policy rule
HIGH_CHURN_PROBABILITY = 0.70                   # nguong agent coi la khach sap roi bo
HIGH_VALUE_TOI = 500_000_000                    # TOI (VND/nam) coi la khach gia tri cao


# --- Buoc 1: GraphState ---------------------------------------------------
class GraphState(TypedDict, total=False):
    """State di xuyen suot graph va song sot qua luc graph bi interrupt."""

    # 5 field bat buoc theo yeu cau Lab
    customer_id: str
    proposed_action: str
    confidence_score: float
    reasoning: str
    human_decision: str | None

    # Field ho tro cho execution + audit
    customer_data: dict[str, Any]
    action_params: dict[str, Any]     # tham so action, human co the Edit
    reviewer_id: str
    executed: bool
    execution_result: str
    agent_override: dict[str, Any]    # chi dung de test routing thu cong


# --- Mock customer data ---------------------------------------------------
# toi               : Total Operating Income cua khach (VND/nam)
# churn_probability : xac suat roi bo
# data_completeness : do day du du lieu -> anh huong confidence cua agent
CUSTOMER_DB: dict[str, dict[str, Any]] = {
    "CUST001": {
        "name": "Nguyen Van A",
        "toi": 720_000_000,
        "churn_probability": 0.82,
        "data_completeness": 0.91,
        "tenure_months": 54,
    },
    "CUST002": {
        "name": "Tran Thi B",
        "toi": 180_000_000,
        "churn_probability": 0.55,
        "data_completeness": 0.92,
        "tenure_months": 18,
    },
    "CUST003": {
        "name": "Le Van C",
        "toi": 240_000_000,
        "churn_probability": 0.45,
        "data_completeness": 0.63,   # du lieu thieu -> confidence tut xuong 0.82
        "tenure_months": 7,
    },
    "CUST004": {
        "name": "Pham Thi D",
        "toi": 1_250_000_000,
        "churn_probability": 0.88,
        "data_completeness": 1.00,   # confidence 0.99 nhung van phai human review
        "tenure_months": 96,
    },
}

DEFAULT_CUSTOMER: dict[str, Any] = {
    "name": "Unknown",
    "toi": 120_000_000,
    "churn_probability": 0.30,
    "data_completeness": 0.70,
    "tenure_months": 3,
}


def _suggested_limit_increase(toi: int) -> int:
    """Mock rule: de xuat tang han muc ~8% TOI, lam tron toi 10 trieu."""
    raw = int(toi * 0.08)
    return max(10_000_000, round(raw / 10_000_000) * 10_000_000)


# --- Buoc 2: Agent reasoning node ----------------------------------------
def evaluate_customer(state: GraphState) -> dict[str, Any]:
    """Mock LLM agent: doc TOI + churn probability -> de xuat action.

    Tra ve proposed_action, confidence_score va reasoning.
    Confidence o day la self-reported (giong LLM tu cham diem minh) nen
    KHONG duoc phep bypass hard policy rule o route_action.
    """
    customer_id = state["customer_id"]
    data = state.get("customer_data") or CUSTOMER_DB.get(customer_id, DEFAULT_CUSTOMER)

    toi = data["toi"]
    churn = data["churn_probability"]
    completeness = data.get("data_completeness", 1.0)

    if churn >= HIGH_CHURN_PROBABILITY and toi >= HIGH_VALUE_TOI:
        proposed_action = "increase_credit_limit"
        action_params = {
            "amount": _suggested_limit_increase(toi),
            "currency": "VND",
        }
        base_confidence = 0.99
        reasoning = (
            f"Khach hang {customer_id} co churn probability {churn:.2f} "
            f"(>= {HIGH_CHURN_PROBABILITY}) va TOI {toi:,} VND (khach gia tri cao). "
            f"Tang han muc tin dung them {action_params['amount']:,} VND duoc ky vong "
            f"giu chan khach hang nay."
        )
    else:
        proposed_action = "send_email"
        action_params = {
            "template": "retention_offer_v2",
            "channel": "email",
        }
        base_confidence = 0.95
        reasoning = (
            f"Khach hang {customer_id} co churn probability {churn:.2f} va "
            f"TOI {toi:,} VND, chua toi nguong can hanh dong tai chinh rui ro cao. "
            f"Gui email chuong trinh giu chan la buoc phu hop va it rui ro."
        )

    # Du lieu cang thieu, agent cang kem chac chan.
    confidence = round(max(0.0, min(1.0, base_confidence - 0.35 * (1 - completeness))), 2)
    if completeness < 0.80:
        reasoning += (
            f" Luu y: du lieu khach hang chi day du {completeness:.0%} nen "
            f"confidence bi ha xuong {confidence}."
        )

    # Chi phuc vu viec test routing thu cong tu UI / run_checks.py.
    override = state.get("agent_override") or {}
    if override.get("proposed_action"):
        proposed_action = override["proposed_action"]
        action_params = override.get("action_params", action_params)
        reasoning = f"[MANUAL OVERRIDE cho muc dich test] {reasoning}"
    if override.get("confidence_score") is not None:
        confidence = float(override["confidence_score"])

    return {
        "proposed_action": proposed_action,
        "confidence_score": confidence,
        "reasoning": reasoning,
        "customer_data": data,
        "action_params": action_params,
    }


# --- Buoc 3: Confidence routing + hard rules ------------------------------
def route_action(state: GraphState) -> str:
    """Conditional edge: quyet dinh node tiep theo.

    Rule 1 - Policy Override : action high-risk -> LUON human review.
    Rule 2 - Auto-Execute    : low-risk + confidence >= threshold -> tu chay.
    Rule 3 - Escalate        : confidence < threshold -> ep human review.
    """
    action = state["proposed_action"]
    confidence = state["confidence_score"]

    # Rule 1 phai duoc kiem tra TRUOC confidence, neu khong confidence 0.99
    # se nuot mat hard policy.
    if action in HIGH_RISK_ACTIONS:
        return "execute_high_risk_action"

    # Rule 2
    if confidence >= CONFIDENCE_THRESHOLD:
        return "execute_low_risk_action"

    # Rule 3
    return "execute_high_risk_action"


def explain_route(state: GraphState) -> tuple[str, str]:
    """Tra ve (node tiep theo, ly do) - dung cho UI va log cho de hieu."""
    action = state["proposed_action"]
    confidence = state["confidence_score"]
    target = route_action(state)
    if action in HIGH_RISK_ACTIONS:
        why = (
            f"Rule 1 - Policy Override: '{action}' la high-risk action nen bat buoc "
            f"human review du confidence = {confidence}."
        )
    elif confidence >= CONFIDENCE_THRESHOLD:
        why = (
            f"Rule 2 - Auto-Execute: '{action}' la low-risk va confidence "
            f"{confidence} >= {CONFIDENCE_THRESHOLD}."
        )
    else:
        why = (
            f"Rule 3 - Escalate: confidence {confidence} < {CONFIDENCE_THRESHOLD} "
            f"nen '{action}' van phai qua human review."
        )
    return target, why


# --- Buoc 6: Execution nodes + audit log ----------------------------------
def execute_low_risk_action(state: GraphState) -> dict[str, Any]:
    """Chay thang action low-risk, khong can con nguoi."""
    action = state["proposed_action"]
    params = state.get("action_params", {})
    result = f"AUTO-EXECUTED '{action}' cho {state['customer_id']} voi params {params}"

    append_audit_entry(
        make_audit_entry(
            agent_id=AGENT_ID,
            action=action,
            confidence=state["confidence_score"],
            reviewer_id="system",          # khong co con nguoi trong nhanh nay
            decision="auto_execute",
            customer_id=state["customer_id"],
            reasoning=state.get("reasoning"),
            action_params=params,
            executed=True,
            note="Low-risk action, confidence dat nguong auto-execute.",
        )
    )
    return {"executed": True, "execution_result": result}


def execute_high_risk_action(state: GraphState) -> dict[str, Any]:
    """Chi chay SAU khi graph duoc resume, dua tren quyet dinh cua con nguoi.

    Node nay nam sau `interrupt_before` nen tai thoi diem no chay,
    state["human_decision"] da duoc Streamlit ghi vao bang update_state().
    """
    decision = (state.get("human_decision") or "no_decision").lower()
    reviewer_id = state.get("reviewer_id") or "unknown_operator"
    action = state["proposed_action"]      # da la action DA SUA neu human Edit
    params = state.get("action_params", {})
    customer_id = state["customer_id"]

    if decision == "approve":
        executed = True
        result = f"EXECUTED '{action}' cho {customer_id} voi params {params} (approved)"
        note = "Human approve nguyen ban de xuat cua agent."
    elif decision == "edit":
        executed = True
        result = f"EXECUTED '{action}' cho {customer_id} voi params {params} (edited)"
        note = "Human chinh sua proposed action truoc khi cho chay."
    elif decision == "reject":
        executed = False
        result = f"ABORTED '{action}' cho {customer_id} - human reject"
        note = "Human tu choi, khong thay doi gi tren he thong."
    else:
        # Fail-safe: khong co quyet dinh cua nguoi thi khong duoc hanh dong.
        executed = False
        result = f"ABORTED '{action}' cho {customer_id} - thieu human decision"
        note = "Khong tim thay human_decision, mac dinh abort de an toan."

    append_audit_entry(
        make_audit_entry(
            agent_id=AGENT_ID,
            action=action,
            confidence=state["confidence_score"],
            reviewer_id=reviewer_id,
            decision=decision,
            customer_id=customer_id,
            reasoning=state.get("reasoning"),
            action_params=params,
            executed=executed,
            note=note,
        )
    )
    return {"executed": executed, "execution_result": result}


# --- Buoc 4: Compile graph voi MemorySaver + interrupt_before -------------
def build_builder() -> StateGraph:
    builder = StateGraph(GraphState)
    builder.add_node("evaluate_customer", evaluate_customer)
    builder.add_node("execute_low_risk_action", execute_low_risk_action)
    builder.add_node("execute_high_risk_action", execute_high_risk_action)

    builder.add_edge(START, "evaluate_customer")
    builder.add_conditional_edges(
        "evaluate_customer",
        route_action,
        {
            "execute_low_risk_action": "execute_low_risk_action",
            "execute_high_risk_action": "execute_high_risk_action",
        },
    )
    builder.add_edge("execute_low_risk_action", END)
    builder.add_edge("execute_high_risk_action", END)
    return builder


def build_graph():
    """Compile graph: checkpointer giu state, interrupt chan high-risk action."""
    memory = MemorySaver()
    return build_builder().compile(
        checkpointer=memory,
        interrupt_before=["execute_high_risk_action"],
    )


def initial_state(customer_id: str, reviewer_id: str = "operator_01", **extra: Any) -> GraphState:
    state: GraphState = {
        "customer_id": customer_id,
        "proposed_action": "",
        "confidence_score": 0.0,
        "reasoning": "",
        "human_decision": None,
        "reviewer_id": reviewer_id,
        "executed": False,
        "execution_result": "",
    }
    state.update(extra)  # type: ignore[typeddict-item]
    return state


if __name__ == "__main__":
    # Demo nhanh bang CLI: chay CUST001 (high-risk) roi approve.
    graph = build_graph()
    config = {"configurable": {"thread_id": "cli-demo"}}

    graph.invoke(initial_state("CUST001"), config)
    snapshot = graph.get_state(config)
    print("Pending node :", snapshot.next)
    print("Proposed     :", snapshot.values["proposed_action"], snapshot.values.get("action_params"))
    print("Confidence   :", snapshot.values["confidence_score"])
    print("Reasoning    :", snapshot.values["reasoning"])

    graph.update_state(config, {"human_decision": "approve"})
    final = graph.invoke(None, config)
    print("Result       :", final["execution_result"])
