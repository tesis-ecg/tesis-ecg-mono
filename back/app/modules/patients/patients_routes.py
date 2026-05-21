from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Patients stub")
async def patients_stub() -> dict[str, bool]:
    return {"todo": True}
