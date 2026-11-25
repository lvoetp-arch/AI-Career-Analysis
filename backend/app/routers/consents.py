from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.services.s2 import privacy
from app.services.s2.compat import current_user, get_store

router = APIRouter(prefix="/api", tags=["consents"])


class ConsentIn(BaseModel):
    consent_type: str
    version: str


@router.post("/consents", status_code=201)
def create_consent(body: ConsentIn, user_id: str = Depends(current_user), store=Depends(get_store)):
    return privacy.record_consent(store, user_id, body.consent_type, body.version)


@router.get("/consents")
def get_consents(user_id: str = Depends(current_user), store=Depends(get_store)):
    return privacy.list_consents(store, user_id)


@router.get("/consents/current")
def get_current_consent(response: Response, user_id: str = Depends(current_user), store=Depends(get_store)):
    response.headers["Cache-Control"] = "no-store"
    version = privacy.PRIVACY_VERSION
    current = store.find_consent(user_id, privacy.CONSENT_TYPE, version)
    return {"version": version, "accepted_at": current["accepted_at"] if current else None}
