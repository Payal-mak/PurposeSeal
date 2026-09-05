from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.user import UserRole


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, description="Unique login identity")
    password: str = Field(..., min_length=8, description="Plaintext password; never stored or logged")
    role: UserRole = Field(..., description="Determines which operations this user may attempt at all")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    created_at: datetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: UserRole
