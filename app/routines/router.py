"""루틴 도메인 라우터. HTTP 입출력만 담당한다."""
from fastapi import APIRouter, Depends

from app import deps
from app.core.schemas import ApiResponse
from app.routines import service
from app.routines.schemas import RoutineData, RoutineGenerateRequest

# 라우터의 모든 엔드포인트에 액세스 토큰 인증을 강제한다
router = APIRouter(
    prefix="/ai/v1",
    tags=["routines"],
    dependencies=[Depends(deps.get_current_user_id)],
)


@router.post("/routines", response_model=ApiResponse[RoutineData])
async def generate_routine(
    request_body: RoutineGenerateRequest,
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[RoutineData]:
    """AI 루틴 생성 요청을 받아 서비스 결과를 팀 규약 응답으로 반환한다."""
    routine = await service.generate_routine(user_id, request_body)
    return ApiResponse(trace_id=trace_id, data=RoutineData(routine=routine))
