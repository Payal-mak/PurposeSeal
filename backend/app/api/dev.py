"""Simulation/development-only endpoints.

Everything in this router is demo scaffolding, not business
functionality: it lets the abstracted clock be advanced over HTTP so a
purpose expiry can be shown in seconds instead of real minutes. It is
registered only when `settings.enable_dev_endpoints` is true (the
default for this hackathon build) — see app/api/__init__.py and
app/core/config.py. A real deployment should set
PURPOSESEAL_ENABLE_DEV_ENDPOINTS=false to remove it entirely.
"""

from fastapi import APIRouter

from ..core.clock import clock
from ..schemas.dev import ClockAdvanceRequest, ClockStateOut

router = APIRouter(prefix="/dev", tags=["dev (simulation only)"])


@router.get("/clock", response_model=ClockStateOut)
def get_clock() -> ClockStateOut:
    return ClockStateOut(now=clock.now())


@router.post("/clock/advance", response_model=ClockStateOut)
def advance_clock(payload: ClockAdvanceRequest) -> ClockStateOut:
    new_now = clock.advance(seconds=payload.seconds, minutes=payload.minutes, hours=payload.hours)
    return ClockStateOut(now=new_now)
