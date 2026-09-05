import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..models.audit_log import AuditLog


def write_audit_log(
    db: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: Any,
    details: Optional[dict] = None,
) -> AuditLog:
    entry = AuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=json.dumps(details) if details is not None else None,
        created_at=clock.now(),
    )
    db.add(entry)
    db.flush()
    return entry
