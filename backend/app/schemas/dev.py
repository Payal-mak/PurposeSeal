from datetime import datetime

from pydantic import BaseModel, Field


class ClockAdvanceRequest(BaseModel):
    minutes: float = Field(default=0, ge=0)
    seconds: float = Field(default=0, ge=0)
    hours: float = Field(default=0, ge=0)


class ClockStateOut(BaseModel):
    now: datetime
