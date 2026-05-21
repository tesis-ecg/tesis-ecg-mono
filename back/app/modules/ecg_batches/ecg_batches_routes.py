from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="ECG batches stub")
async def ecg_batches_stub() -> dict[str, bool]:
    return {"todo": True}
