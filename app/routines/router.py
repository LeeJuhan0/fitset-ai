"""루틴 도메인 라우터 — HTTP 입출력만. 클라이언트 API 명세 §1 (POST /v1/routines)."""
from fastapi import APIRouter, Depends

from app import deps
from app.core.schemas import ApiResponse
from app.routines import service
from app.routines.schemas import RoutineData, RoutineGenerateRequest

router = APIRouter(prefix="/v1", tags=["routines"])


@router.post("/routines", response_model=ApiResponse[RoutineData])
async def generate_routine(
    request_body: RoutineGenerateRequest,
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[RoutineData]:
    routine = await service.generate_routine(user_id, request_body)
    return ApiResponse(trace_id=trace_id, data=RoutineData(routine=routine))
