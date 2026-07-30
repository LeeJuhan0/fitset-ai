"""운동 도메인 라우터. HTTP 입출력만 담당한다."""
from fastapi import APIRouter, Depends

from app import deps
from app.core.schemas import ApiResponse
from app.exercises import service
from app.exercises.schemas import ExerciseVideoOut

# 스레드와 무관한 종목 단위 조회지만 액세스 토큰 인증은 동일하게 요구한다 (§6-B)
router = APIRouter(
    prefix="/v1",
    tags=["exercises"],
    dependencies=[Depends(deps.get_current_user_id)],
)


@router.get("/exercises/{slug}/video", response_model=ApiResponse[ExerciseVideoOut])
async def reissue_video_url(
    slug: str,
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ExerciseVideoOut]:
    """만료된 가이드 영상 presigned URL을 재발급한다."""
    video = await service.get_video(slug)
    return ApiResponse(trace_id=trace_id, data=ExerciseVideoOut(**video))
