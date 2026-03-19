"""
/api/refresh  — trigger incremental collection from the UI

Uses a shared volume (/trigger) to signal the collector.
The collector polls for /trigger/refresh file.
"""
import pathlib
from fastapi import APIRouter

TRIGGER_FILE = pathlib.Path("/trigger/refresh")
router = APIRouter()


@router.post("")
async def trigger_refresh():
    TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIGGER_FILE.touch()
    return {"triggered": True}
