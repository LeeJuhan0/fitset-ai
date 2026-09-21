from app.core.schemas import CamelModel


class ExerciseVideoOut(CamelModel):
    exercise_id: str | None
    slug: str
    exercise_name: str
    video_url: str
    expires_at: str | None
