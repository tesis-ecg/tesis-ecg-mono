from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Admin stub")
async def admin_stub() -> dict[str, bool]:
    return {"todo": True}
