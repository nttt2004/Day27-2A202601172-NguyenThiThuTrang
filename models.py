"""Bước 1 (phần Audit) - Audit schema + helper ghi audit trail ra file JSON.

AuditEntry là "hộp đen" của hệ thống HITL: mỗi quyết định quan trọng
(agent tự chạy hoặc human approve/reject/edit) đều phải để lại một dòng
truy vết được.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Audit trail nằm cạnh source code cho dễ kiểm tra khi chấm bài.
AUDIT_LOG_PATH = Path(__file__).with_name("audit_log.json")


class AuditEntry(BaseModel):
    """Một bản ghi audit: agent đề xuất gì, ai review và quyết định thế nào."""

    # 6 field bắt buộc theo yêu cầu Lab
    timestamp: str
    agent_id: str
    action: str
    confidence: float
    reviewer_id: str
    decision: str

    # Field bổ sung để audit trail thực sự dùng được khi điều tra sự cố
    customer_id: str | None = None
    reasoning: str | None = None
    action_params: dict[str, Any] = Field(default_factory=dict)
    executed: bool = False
    note: str | None = None


def make_audit_entry(
    *,
    agent_id: str,
    action: str,
    confidence: float,
    reviewer_id: str,
    decision: str,
    **extra: Any,
) -> AuditEntry:
    """Tạo AuditEntry với timestamp ISO của thời điểm quyết định."""
    return AuditEntry(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        agent_id=agent_id,
        action=action,
        confidence=confidence,
        reviewer_id=reviewer_id,
        decision=decision,
        **extra,
    )


def load_audit_log(path: Path | str = AUDIT_LOG_PATH) -> list[dict[str, Any]]:
    """Đọc audit trail hiện có. File hỏng/không tồn tại -> trả về list rỗng."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def append_audit_entry(
    entry: AuditEntry, path: Path | str = AUDIT_LOG_PATH
) -> list[dict[str, Any]]:
    """Append-only: đọc lịch sử cũ -> thêm entry mới -> ghi lại cả danh sách.

    Tuyệt đối không ghi đè một object mới lên toàn bộ lịch sử.
    Trong production nên thay bằng bảng append-only trong PostgreSQL.
    """
    path = Path(path)
    entries = load_audit_log(path)
    entries.append(entry.model_dump())
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entries
