"""Admin routes: list/approve/reject users."""

from fastapi import APIRouter, Depends

from dependencies import require_admin
from services.auth_service import approve_user, get_all_users, reject_user

router = APIRouter(tags=["admin"])


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    return await get_all_users()


@router.post("/users/{user_id}/approve")
async def approve(user_id: str, admin: dict = Depends(require_admin)):
    return await approve_user(user_id)


@router.post("/users/{user_id}/reject")
async def reject(user_id: str, admin: dict = Depends(require_admin)):
    return await reject_user(user_id)
