from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Alerts stub")
async def alerts_stub() -> dict[str, bool]:
    return {"todo": True}
