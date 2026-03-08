"""FastAPI dependencies for authentication and authorization."""

from fastapi import Depends, Header, HTTPException

from services.auth_service import get_profile_by_token


async def get_current_user(authorization: str = Header(None)) -> dict:
    """Validate Bearer token and return the user's profile.

    Raises 401 if token is missing/invalid.
    Raises 403 if account is not yet approved.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Kimlik doğrulama gerekli.")
    token = authorization.split(" ", 1)[1]
    return await get_profile_by_token(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Ensure the current user has admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin yetkisi gerekli.")
    return user
