"""Buoc 5 - Streamlit approval interface cho HITL workflow.

Chay:  streamlit run app.py
"""

from __future__ import annotations

import json
from uuid import uuid4

import streamlit as st

from graph import (
    CONFIDENCE_THRESHOLD,
    CUSTOMER_DB,
    HIGH_RISK_ACTIONS,
    build_graph,
    explain_route,
    initial_state,
)
from models import AUDIT_LOG_PATH, load_audit_log

st.set_page_config(page_title="Day27 - HITL Churn Risk Review", page_icon="🛑", layout="wide")


# --- Graph duoc cache o cap process ---------------------------------------
@st.cache_resource
def get_graph():
    """Cache compiled graph, neu khong MemorySaver se bi tao lai moi rerun
    va state dang pending se bien mat."""
    return build_graph()


graph = get_graph()

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None


def current_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def resume_with(decision: str, **updates) -> None:
    """Ghi quyet dinh cua human vao state roi resume graph."""
    config = current_config()
    graph.update_state(config, {"human_decision": decision, **updates})
    graph.invoke(None, config)          # resume tu diem bi interrupt
    st.rerun()


# --- Sidebar: khoi tao mot workflow run -----------------------------------
with st.sidebar:
    st.header("Khoi tao review")
    reviewer_id = st.text_input("Reviewer ID", value="operator_01")
    customer_id = st.selectbox(
        "Customer",
        list(CUSTOMER_DB.keys()),
        format_func=lambda cid: f"{cid} - {CUSTOMER_DB[cid]['name']}",
    )

    profile = CUSTOMER_DB[customer_id]
    st.caption(
        f"TOI: {profile['toi']:,} VND | churn: {profile['churn_probability']:.2f} | "
        f"data completeness: {profile['data_completeness']:.0%}"
    )

    with st.expander("Test routing thu cong (tuy chon)"):
        use_override = st.checkbox("Ep proposed_action / confidence")
        override_action = st.selectbox(
            "proposed_action", ["send_email", "increase_credit_limit"], disabled=not use_override
        )
        override_conf = st.slider(
            "confidence_score", 0.0, 1.0, 0.99, 0.01, disabled=not use_override
        )

    if st.button("Chay danh gia khach hang", type="primary", use_container_width=True):
        st.session_state.thread_id = f"{customer_id}-{uuid4().hex[:6]}"
        state = initial_state(customer_id, reviewer_id)
        if use_override:
            state["agent_override"] = {
                "proposed_action": override_action,
                "confidence_score": override_conf,
            }
        graph.invoke(state, current_config())
        st.rerun()

    st.divider()
    st.caption(f"Confidence threshold: **{CONFIDENCE_THRESHOLD}**")
    st.caption(f"Hard-rule actions: **{', '.join(sorted(HIGH_RISK_ACTIONS))}**")
    if st.session_state.thread_id:
        st.caption(f"thread_id: `{st.session_state.thread_id}`")


st.title("Human-in-the-Loop - Churn Risk Review")

if not st.session_state.thread_id:
    st.info("Chon mot khach hang o sidebar va bam **Chay danh gia khach hang**.")
    st.stop()

snapshot = graph.get_state(current_config())
values = snapshot.values
pending = "execute_high_risk_action" in snapshot.next

# --- Action card ----------------------------------------------------------
target, why = explain_route(values)
st.subheader("Agent proposal")
c1, c2, c3 = st.columns([1, 1, 1])
c1.metric("Customer ID", values["customer_id"])
c2.metric("Confidence", f"{values['confidence_score']:.2f}")
c3.metric("Risk", "HIGH" if target == "execute_high_risk_action" else "LOW")

st.write("**Proposed action:**")
st.code(values["proposed_action"], language=None)
st.write("**Action params:**")
st.json(values.get("action_params", {}))
st.write("**Reasoning:**")
st.info(values["reasoning"])
st.write("**Routing decision:**")
if target == "execute_high_risk_action":
    st.warning(why)
else:
    st.success(why)

st.divider()

# --- Human review ---------------------------------------------------------
if pending:
    st.subheader("⏸️ Graph dang PENDING - cho human review")
    st.caption(
        "Node `execute_high_risk_action` CHUA chay. Graph dung lai nho "
        "`interrupt_before`, state van con nguyen trong MemorySaver."
    )

    col_a, col_r = st.columns(2)
    if col_a.button("✅ Approve", use_container_width=True, type="primary"):
        resume_with("approve", reviewer_id=reviewer_id)
    if col_r.button("❌ Reject", use_container_width=True):
        resume_with("reject", reviewer_id=reviewer_id)

    with st.expander("✏️ Edit truoc khi approve"):
        with st.form("edit_form"):
            new_action = st.text_input("proposed_action", value=values["proposed_action"])
            new_params = st.text_area(
                "action_params (JSON)",
                value=json.dumps(values.get("action_params", {}), ensure_ascii=False, indent=2),
                height=140,
            )
            submitted = st.form_submit_button("Luu chinh sua va chay")
        if submitted:
            try:
                parsed = json.loads(new_params)
            except json.JSONDecodeError as exc:
                st.error(f"action_params khong phai JSON hop le: {exc}")
            else:
                resume_with(
                    "edit",
                    reviewer_id=reviewer_id,
                    proposed_action=new_action,
                    action_params=parsed,
                )
else:
    decision = values.get("human_decision") or "auto_execute"
    st.subheader("✔️ Workflow da ket thuc")
    st.write(f"**Human decision:** `{decision}`")
    if values.get("executed"):
        st.success(values.get("execution_result", ""))
    else:
        st.error(values.get("execution_result", ""))

# --- Audit trail ----------------------------------------------------------
st.divider()
st.subheader("Audit trail")
st.caption(f"File: `{AUDIT_LOG_PATH}`")
entries = load_audit_log()
if entries:
    st.dataframe(
        [
            {
                "timestamp": e["timestamp"],
                "customer_id": e.get("customer_id"),
                "agent_id": e["agent_id"],
                "action": e["action"],
                "confidence": e["confidence"],
                "reviewer_id": e["reviewer_id"],
                "decision": e["decision"],
                "executed": e.get("executed"),
            }
            for e in reversed(entries)
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.caption("Chua co audit entry nao.")
