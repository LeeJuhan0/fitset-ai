"""SQLAlchemy 선언 베이스 — 백엔드 MySQL 스키마 사본 엔티티의 공용 메타데이터. core 레이어.

엔티티는 각 도메인 패키지의 domain.py에 산다 (users, workouts, exercises).
패키지 간 FK(예: workout_exercise → exercise.id)가 한 메타데이터 안에서 풀리도록
베이스만 여기서 공유한다. 이 엔티티들로 쓰기를 만들지 않는다 — 읽기 전용 조회 전용.
"""
from sqlalchemy.orm import DeclarativeBase


class BackendBase(DeclarativeBase):
    """백엔드 스키마 전용 베이스 — 형태의 정본은 docs/백엔드 ERD.md (2026-08-12 공유본)."""
