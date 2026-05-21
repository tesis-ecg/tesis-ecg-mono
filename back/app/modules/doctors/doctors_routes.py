from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Doctors stub")
async def doctors_stub() -> dict[str, bool]:
    return {"todo": True}
