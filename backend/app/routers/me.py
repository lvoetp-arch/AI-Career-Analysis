from fastapi import APIRouter, Depends

from app.services.s2 import privacy
from app.services.s2.compat import current_user, get_store

router = APIRouter(prefix="/api/me", tags=["me"])


@router.delete("/data")
def delete_my_data(user_id: str = Depends(current_user), store=Depends(get_store)):
    return {"deleted": privacy.delete_user_data(store, user_id)}
