from fastapi import Request, HTTPException
from schemas.auth import UserContext

def get_current_user_context(request: Request) -> UserContext:
    if not hasattr(request.state, "user"):
         raise HTTPException(status_code=401, detail="Not authenticated")
    return request.state.user
