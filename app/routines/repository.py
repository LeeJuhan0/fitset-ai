"""루틴 저장소 — 부팅 시 DynamoDB `routines` Scan → 라이트 인메모리 로드(임베딩 포함).

메모리에는 룰 필터·LLM 프롬프트에 필요한 필드(부위·수준·시간·장비·종목명 요약)와
정규화 임베딩 행렬만 유지한다(~350MB). 세트 상세가 담긴 전체 루틴은 최종 선택된
1건만 GetItem으로 가져온다 — 다중 조건 필터를 DynamoDB 쿼리로 하지 않는다.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.core.config import get_settings
from app.core.dynamo import get_routines_table, to_plain


def _parse_embedding(raw, dimension: int) -> np.ndarray | None:
    if raw is None:
        return None
    vector = np.frombuffer(bytes(raw), dtype="<f4")
    if vector.shape[0] != dimension:
        return None
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return None
    return vector / norm


def _to_light(item: dict) -> dict:
    """전체 항목 → 라이트 항목 — 룰 필터 필드 + LLM 프롬프트용 종목명 요약만 남긴다."""
    minutes = item.get("minutes_per_routine")
    return {
        "slug": item["slug"],
        "level": item.get("level", "beginner"),
        "goal": item.get("goal"),
        "muscle_groups": list(item.get("muscle_groups", [])),
        "equipment": list(item.get("equipment", [])),
        "minutes_per_routine": int(minutes) if minutes else None,
        "exercise_names": [e["exercise_name"] for e in item.get("exercises", [])],
    }


class RoutineStore:
    """라이트 인메모리 루틴 + 정규화 임베딩 행렬. ready 전에는 서빙 불가(헬스체크 연동)."""

    def __init__(self):
        self.routines: list[dict] = []
        self.vectors: np.ndarray | None = None
        self.ready = False

    def load(self) -> None:
        """전량 Scan → 라이트 리스트 + (N, dim) 정규화 임베딩 행렬 구성. 동기 — 스레드에서 호출."""
        settings = get_settings()
        table = get_routines_table()
        routines: list[dict] = []
        vector_chunks: list[np.ndarray] = []
        kwargs: dict = {}
        while True:
            page = table.scan(**kwargs)
            for item in page["Items"]:
                embedding = _parse_embedding(item.get("embedding"), settings.embed_dimension)
                vector_chunks.append(
                    embedding if embedding is not None
                    else np.zeros(settings.embed_dimension, dtype=np.float32)
                )
                routines.append(_to_light(item))
            if settings.routines_scan_limit and len(routines) >= settings.routines_scan_limit:
                routines = routines[: settings.routines_scan_limit]
                vector_chunks = vector_chunks[: settings.routines_scan_limit]
                break
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

        self.routines = routines
        self.vectors = np.vstack(vector_chunks) if vector_chunks else np.zeros(
            (0, settings.embed_dimension), dtype=np.float32
        )
        self.ready = True

    def get_full(self, slug: str) -> dict | None:
        """전체 루틴(세트 상세 포함) 단건 조회 — 최종 선택된 루틴에만 사용. GetItem 1회."""
        item = get_routines_table().get_item(Key={"slug": slug}).get("Item")
        if item is None:
            return None
        item.pop("embedding", None)
        item.pop("embedding_model", None)
        return to_plain(item)


@lru_cache
def get_routine_store() -> RoutineStore:
    return RoutineStore()


@lru_cache
def get_exercise_meta() -> dict[str, dict]:
    """종목 마스터 206종 (repo 동봉 metadata) — slug → 항목."""
    path = Path(get_settings().exercise_metadata_path)
    entries = json.loads(path.read_text())
    return {entry["slug"]: entry for entry in entries}
