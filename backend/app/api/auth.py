from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.security import create_access_token
from ..db.session import get_db
from ..models.user import User
from ..schemas.auth import LoginRequest, TokenOut, UserOut, UserRegister
from ..services import auth_service
from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    user = auth_service.register_user(db, payload)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, payload.username, payload.password)
    token = create_access_token(subject=user.username, role=user.role.value)
    return TokenOut(access_token=token, username=user.username, role=user.role)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
