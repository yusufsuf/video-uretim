"""Auth routes: register, login, me."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dependencies import get_current_user
from services.auth_service import login_user, register_user

router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/register")
async def register(req: RegisterRequest):
    return await register_user(req.email, req.password, req.full_name)


@router.post("/login")
async def login(req: LoginRequest):
    return await login_user(req.email, req.password)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user
