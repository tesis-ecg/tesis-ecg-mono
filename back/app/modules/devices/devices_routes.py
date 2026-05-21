from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Devices stub")
async def devices_stub() -> dict[str, bool]:
    return {"todo": True}
