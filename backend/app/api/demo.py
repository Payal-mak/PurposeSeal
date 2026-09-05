"""Deterministic, demo/simulation-only scenario runner endpoints.

Each endpoint drives one complete, self-contained PurposeSeal journey —
create asset, grant, retrieve, copy/derive, evaluate use — entirely
through the same services every other endpoint uses (see
app/services/demo_service.py), with canned actors/purposes and the
abstracted clock standing in for real time. No randomness, no network
calls, no manual setup required.

Registered only when `settings.enable_dev_endpoints` is true, the same
gate as app/api/dev.py, since this exists purely to make demoing the
product fast and repeatable, not as product functionality. A real
deployment should set PURPOSESEAL_ENABLE_DEV_ENDPOINTS=false to remove
it entirely.
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.demo import DemoScenarioOut, TimelineEventOut
from ..services import demo_service
from ..services.demo_service import DemoScenarioResult

router = APIRouter(prefix="/demo/scenarios", tags=["demo (simulation only)"])


def _to_out(result: DemoScenarioResult) -> DemoScenarioOut:
    return DemoScenarioOut(
        scenario=result.scenario,
        decision=result.decision,
        reason_code=result.reason_code,
        reason=result.reason,
        asset_id=result.asset_id,
        grant_id=result.grant_id,
        remediation=result.remediation,
        remediation_status=result.remediation_status,
        timeline=[
            TimelineEventOut(
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                details=json.loads(event.details) if event.details else None,
                created_at=event.created_at,
            )
            for event in result.timeline
        ],
    )


@router.post("/legitimate", response_model=DemoScenarioOut)
def run_legitimate_scenario(db: Session = Depends(get_db)):
    return _to_out(demo_service.run_legitimate_scenario(db))


@router.post("/expired", response_model=DemoScenarioOut)
def run_expired_scenario(db: Session = Depends(get_db)):
    return _to_out(demo_service.run_expired_scenario(db))


@router.post("/purpose-mismatch", response_model=DemoScenarioOut)
def run_purpose_mismatch_scenario(db: Session = Depends(get_db)):
    return _to_out(demo_service.run_purpose_mismatch_scenario(db))
