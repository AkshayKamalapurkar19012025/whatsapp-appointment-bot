from fastapi import APIRouter

from app.config import DEFAULT_TIMEZONE

router = APIRouter()


@router.get("/app-config")
def get_app_config():
    """
    Public, non-sensitive display configuration for the patient site --
    currently just the clinic's default IANA timezone, so the frontend
    never has to hardcode one (see app/config.py's DEFAULT_TIMEZONE).
    """
    return {"default_timezone": DEFAULT_TIMEZONE}
