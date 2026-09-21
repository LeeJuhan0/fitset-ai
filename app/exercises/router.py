from fastapi import APIRouter, Depends, Query

from app import deps
from app.core.schemas import ApiResponse
from app.exercises import service
from app.exercises.schemas import ExerciseVideoOut

router = APIRouter(
    prefix="/ai/v1",
    tags=["exercises"],
    dependencies=[Depends(deps.get_current_user_id)],
)


@router.get("/exercises/{slug}/video", response_model=ApiResponse[ExerciseVideoOut])
async def reissue_video_url(
    slug: str,
    fallback: bool = Query(default=False),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ExerciseVideoOut]:
    """종목 가이드 영상 재생 URL 응답"""
    video = await service.get_video(slug, fallback=fallback)
    return ApiResponse(trace_id=trace_id, data=ExerciseVideoOut(**video))
