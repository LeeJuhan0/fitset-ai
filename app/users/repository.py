from datetime import timedelta

from sqlalchemy import select

from app.clients import mysql
from app.core import clock
from app.exercises.models import Muscle
from app.users.models import BodyWeightHistory, UserAvoidedMuscle, UserProfile
from app.users.exceptions import UserNotFoundError


def get_profile(user_id: str) -> dict:
    """유저 프로필 최신 체중 기피 부위 MySQL 조회"""
    uid = mysql.uuid_bytes(user_id)
    rows = mysql.fetch_all(
        select(
            UserProfile.height,
            UserProfile.gender,
            UserProfile.birth_date,
            UserProfile.workout_goal,
            UserProfile.level,
        ).where(UserProfile.user_id == uid)
    )
    if not rows:
        raise UserNotFoundError()
    profile = rows[0]

    avoided = mysql.fetch_all(
        select(Muscle.slug)
        .select_from(UserAvoidedMuscle)
        .join(Muscle, Muscle.id == UserAvoidedMuscle.muscle_id)
        .where(UserAvoidedMuscle.user_id == uid)
    )
    latest_weight = mysql.fetch_all(
        select(BodyWeightHistory.weight)
        .where(BodyWeightHistory.user_id == uid)
        .order_by(BodyWeightHistory.measured_at.desc())
        .limit(1)
    )
    return {
        "heightCm": float(profile["height"]),
        "weightKg": float(latest_weight[0]["weight"]) if latest_weight else None,
        "gender": profile["gender"],
        "birthDate": profile["birth_date"].isoformat(),
        "goal": mysql.camel_enum(profile["workout_goal"]),
        "level": mysql.camel_enum(profile["level"]),
        "avoidBodyParts": [row["slug"] for row in avoided],
    }


def get_body_weights(user_id: str, days: int) -> list[dict]:
    """기간 내 체중 측정 이력 측정 시각 오름차순 조회"""
    cutoff = (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)
    rows = mysql.fetch_all(
        select(BodyWeightHistory.measured_at, BodyWeightHistory.weight)
        .where(BodyWeightHistory.user_id == mysql.uuid_bytes(user_id), BodyWeightHistory.measured_at >= cutoff)
        .order_by(BodyWeightHistory.measured_at)
    )
    return [
        {"measuredAt": mysql.iso_from_db(row["measured_at"]), "weightKg": float(row["weight"])}
        for row in rows
    ]
