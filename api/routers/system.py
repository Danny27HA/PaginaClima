from fastapi import APIRouter
from ..db import db_version

router = APIRouter(prefix="/system", tags=["system"])

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/db")
def db_info():
    return db_version()
