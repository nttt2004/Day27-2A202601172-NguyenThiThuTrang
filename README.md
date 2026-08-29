# Day 27 - Hệ thống Agent Human-in-the-Loop (HITL)

LangGraph workflow đánh giá **churn risk** của khách hàng: agent đề xuất hành động kèm
confidence score, policy quyết định route, graph **tạm dừng trước hành động rủi ro cao**
để con người Approve / Reject / Edit qua Streamlit, và mọi quyết định được ghi vào audit trail.

```
Customer Data
      |
      v
evaluate_customer  --> proposed_action + confidence_score + reasoning
      |
      v
route_action (hard rules + confidence routing)
      |
      +---------------------------+
      | low-risk & conf >= 0.85   | high-risk hoặc conf < 0.85
      v                           v
execute_low_risk_action     execute_high_risk_action
   (auto execute)                 X  <- interrupt_before: graph DỪNG ở đây
                                  |
                                  v
                          Streamlit review (Approve / Reject / Edit)
                                  |
                          update_state -> invoke(None, config)
                                  |
                                  v
                          Execute hoặc Abort  ->  audit_log.json
```

## 1. Cấu trúc project

| File | Nội dung |
|---|---|
| `graph.py` | `GraphState`, `evaluate_customer`, `route_action`, `execute_low_risk_action`, `execute_high_risk_action`, `MemorySaver` + `interrupt_before`, mock customer DB |
| `models.py` | `AuditEntry` (Pydantic) + helper append-only ghi `audit_log.json` |
| `app.py` | Streamlit approval interface (Approve / Reject / Edit + audit trail) |
| `run_checks.py` | Script tự kiểm tra toàn bộ luồng HITL (không cần UI) |
| `audit_log.json` | Audit trail |
| `requirements.txt` | Dependencies |
| `screenshots/` | Ảnh minh hoạ pending / approved / edited |

## 2. Cài đặt

```bash
pip install -r requirements.txt
```

Yêu cầu Python 3.10+. Project **không dùng API key** (agent là mock LLM deterministic),
nên không cần `.env` và không có credential nào trong repo.

## 3. Chạy LangGraph workflow (CLI)

Demo nhanh một run high-risk rồi approve:

```bash
python graph.py
```

Kết quả mẫu:

```
Pending node : ('execute_high_risk_action',)
Proposed     : increase_credit_limit {'amount': 60000000, 'currency': 'VND'}
Confidence   : 0.96
Result       : EXECUTED 'increase_credit_limit' cho CUST001 ... (approved)
```

Chạy bộ kiểm tra đầy đủ (state, 3 routing rule, interrupt, approve/reject/edit, audit log):

```bash
python run_checks.py
```

Kết quả hiện tại: **PASSED 25 / FAILED 0**.

## 4. Chạy Streamlit UI

```bash
streamlit run app.py
```

1. Ở sidebar: nhập **Reviewer ID**, chọn khách hàng, bấm **Chay danh gia khach hang**.
2. Nếu action bị route sang high-risk, graph dừng và UI hiện **Graph dang PENDING**.
3. Chọn **Approve**, **Reject** hoặc mở **Edit truoc khi approve**.

Mục *Test routing thu cong* trong sidebar cho phép ép `proposed_action` / `confidence_score`
để demo trực tiếp từng rule (ví dụ `increase_credit_limit` @ `0.99` vẫn phải human review).

## 5. Policy đang dùng

| Rule | Điều kiện | Route tới |
|---|---|---|
| **Rule 1 - Policy Override (hard rule)** | `proposed_action` thuộc `HIGH_RISK_ACTIONS = {"increase_credit_limit"}` | `execute_high_risk_action` (human review) — **bất kể confidence**, kể cả 0.99 |
| **Rule 2 - Auto-Execute** | action low-risk **và** `confidence_score >= 0.85` | `execute_low_risk_action` (chạy thẳng) |
| **Rule 3 - Escalate** | `confidence_score < 0.85` | `execute_high_risk_action` (human review) |

- **Confidence threshold: `0.85`** (`CONFIDENCE_THRESHOLD` trong `graph.py`).
- **Hard policy rule:** `increase_credit_limit` luôn phải qua con người. Trong `route_action`,
  hard rule được kiểm tra **trước** ngưỡng confidence — nếu đảo thứ tự, confidence 0.99 sẽ
  nuốt mất policy.
- Graph được compile với `MemorySaver()` và `interrupt_before=["execute_high_risk_action"]`,
  nên node high-risk **chưa từng chạy** khi UI đang hiển thị bản đề xuất; state khách hàng
  vẫn nằm nguyên trong checkpoint trong lúc chờ.

Dữ liệu khách hàng mock (trong `graph.py`) được thiết kế để chạm đủ 3 nhánh:

| Customer | TOI | Churn | Agent đề xuất | Confidence | Kết quả route |
|---|---|---|---|---|---|
| CUST001 | 720,000,000 | 0.82 | `increase_credit_limit` | 0.96 | Human review (Rule 1) |
| CUST002 | 180,000,000 | 0.55 | `send_email` | 0.92 | Auto execute (Rule 2) |
| CUST003 | 240,000,000 | 0.45 | `send_email` | 0.82 | Human review (Rule 3) |
| CUST004 | 1,250,000,000 | 0.88 | `increase_credit_limit` | 0.99 | Human review (Rule 1) |

## 6. Approve / Reject / Edit hoạt động thế nào

Cả ba nút đều đi qua cùng một cơ chế: ghi quyết định vào state rồi resume graph.

```python
graph.update_state(config, {"human_decision": decision, ...})
graph.invoke(None, config)   # resume từ đúng điểm bị interrupt
```

| Hành động | State được ghi | `execute_high_risk_action` làm gì |
|---|---|---|
| **Approve** | `human_decision="approve"` | Thực thi action như agent đề xuất |
| **Reject** | `human_decision="reject"` | Huỷ action, không thay đổi gì |
| **Edit** | `human_decision="edit"` + `proposed_action` / `action_params` đã sửa | Thực thi action **sau khi** đã sửa (ví dụ 60,000,000 -> 20,000,000 VND) |

Nếu không có `human_decision` (fail-safe), node mặc định **abort** — agent không bao giờ được
tự hành động khi thiếu quyết định của con người.

> `app.py` cache compiled graph bằng `@st.cache_resource`. Nếu không cache, mỗi lần Streamlit
> rerun sẽ tạo `MemorySaver` mới và state đang pending biến mất.

## 7. Audit log

Lưu tại **`audit_log.json`** (cùng thư mục source, đường dẫn khai báo ở `models.AUDIT_LOG_PATH`).
Mỗi quyết định — auto-execute, approve, reject, edit — đều sinh một `AuditEntry`:

```json
{
  "timestamp": "2026-08-29T16:30:44",
  "agent_id": "churn-risk-agent",
  "action": "increase_credit_limit",
  "confidence": 0.99,
  "reviewer_id": "operator_01",
  "decision": "approve",
  "customer_id": "CUST004",
  "action_params": { "amount": 100000000, "currency": "VND" },
  "executed": true
}
```

6 field bắt buộc: `timestamp`, `agent_id`, `action`, `confidence`, `reviewer_id`, `decision`
(các field còn lại là bổ sung để điều tra sự cố dễ hơn).

Ghi theo kiểu **append-only**: đọc lịch sử cũ -> append entry mới -> ghi lại cả danh sách,
không bao giờ overwrite. UI hiển thị bảng audit ở cuối trang. Trong production nên thay
file JSON bằng bảng append-only trong PostgreSQL.

## 8. Reflection

**Câu 1 — `interrupt_before` hay `interrupt_after` khi muốn con người sửa email vừa được generate?**

Dùng `interrupt_after` trên node generate email. `interrupt_before` dừng *trước khi* node chạy,
lúc đó email còn chưa tồn tại nên không có gì để sửa. `interrupt_after` để node sinh ra bản nháp,
ghi vào state, rồi mới dừng — con người đọc bản nháp, rewrite, `update_state` và resume, và
routing node phía sau nhận đúng bản đã sửa. Nguyên tắc chung: `interrupt_before` dùng để **chặn
một hành động** sắp xảy ra (side-effect không hoàn tác được), `interrupt_after` dùng để **review
một sản phẩm** vừa được tạo ra.

**Câu 2 — 500 email/ngày bị ép review vì confidence kẹt ở 0.82, chống Alert Fatigue thế nào?**

Vấn đề không phải con người lười mà là hàng đợi chứa toàn việc rủi ro thấp. Các thay đổi cụ thể:

1. **Threshold theo action, không dùng một ngưỡng chung.** `send_email` là hành động hoàn tác được
   nên có thể để ngưỡng 0.70; `increase_credit_limit` giữ hard rule. Ngưỡng đúng phải xuất phát từ
   *chi phí khi sai*, chứ không phải một con số 0.85 cho mọi thứ.
2. **Calibrate confidence trước khi routing** (xem Câu 3) — 0.82 kẹt cứng là dấu hiệu điểm số không
   được hiệu chỉnh, chứ không phải 500 case đều thật sự mơ hồ.
3. **Batch + bulk approve.** Gom các case giống nhau thành nhóm, review theo mẫu đại diện và approve
   cả lô, thay vì 500 card riêng lẻ.
4. **Sampling thay vì review toàn bộ.** Auto-execute và chỉ đưa ngẫu nhiên 5-10% vào hàng đợi để
   kiểm tra chất lượng; theo dõi tỉ lệ reject để quyết định có siết lại ngưỡng không.
5. **Undo window thay cho pre-approval.** Với action hoàn tác được, gửi có trì hoãn 15 phút kèm nút
   thu hồi — con người vẫn kiểm soát nhưng không phải chờ ở đường tới hạn.
6. **Ưu tiên hàng đợi theo rủi ro** (giá trị khách hàng, số tiền, độ bất thường), để việc quan trọng
   không bị chôn dưới hàng trăm việc vặt.

**Câu 3 — Vì sao nguy hiểm khi tin vào confidence LLM tự chấm, và calibrate thế nào?**

Điểm confidence LLM tự báo là *một token được sinh ra*, không phải xác suất đúng được đo lường. Nó
phản ánh mức độ trôi chảy của lập luận chứ không phản ánh chất lượng dữ liệu đầu vào: nếu model đọc
sai thu nhập khách hàng, nó vẫn tự tin 0.95 vì bản thân câu chuyện nó kể rất mạch lạc. LLM nổi tiếng
là overconfident, self-report không có ground truth để hiệu chỉnh, và tệ nhất là điểm số này lại
đang được dùng để quyết định có bỏ qua con người hay không — tức là một con số do chính agent tạo ra
lại điều khiển cái phanh của chính nó.

Cách calibrate trước bước routing:

- **Tách confidence dữ liệu khỏi confidence lập luận.** Trong lab này, `evaluate_customer` hạ điểm
  theo `data_completeness`: dữ liệu thiếu thì confidence tụt (CUST003: 0.95 -> 0.82). Độ tin cậy
  của đầu vào là thứ đo được, không cần hỏi LLM.
- **Verify các con số bằng nguồn sự thật.** Thu nhập, TOI, dư nợ phải đọc từ core banking và
  cross-check với giá trị agent trích; lệch quá ngưỡng thì ép confidence xuống 0 và escalate.
- **Hiệu chỉnh theo lịch sử thực tế.** Dùng chính `audit_log.json`: nếu ở khoảng self-report 0.95 mà
  con người reject 30%, thì ánh xạ 0.95 -> ~0.70 (Platt scaling / isotonic regression trên dữ liệu
  approve-reject).
- **Không cho confidence quyền vượt hard rule.** Đây là lý do Rule 1 được kiểm tra trước Rule 2:
  dù calibrate tốt đến đâu, hành động rủi ro cao vẫn phải có con người.
