"""
Manual scan trigger endpoint.
"""
from fastapi import APIRouter, BackgroundTasks

from backend.models.schemas import ScanResult
from backend.services.scanner import scanner

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("", response_model=ScanResult)
async def trigger_scan(background_tasks: BackgroundTasks):
    """
    Manually trigger a full market scan.
    The scan runs in the background and the endpoint returns immediately with
    a placeholder result; poll /trades and /portfolio for updates.
    """
    result = await scanner.run()
    return result
