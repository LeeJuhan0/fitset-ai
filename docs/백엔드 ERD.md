# 백엔드(Spring) DB ERD — 참조 문서

백엔드 스프링 서버가 보유한 MySQL ERD (2026-07-25 백엔드 공유본).
AI 서버는 이 DB에 직접 접근하지 않는다 — [백엔드 내부 API 명세](백엔드%20내부%20API%20명세.md)를 통해서만 조회.
이 문서는 내부 API 계약·데이터 정합성 검토를 위한 **참조용 사본**이며, 정본은 백엔드 리포.

- DB: MySQL, id는 `BINARY(16)` (UUIDv7 권장)
- 진행 중 세션은 `active_workout*` 계층(본 ERD 범위 밖), 완료 기록은 `workout*` 계층으로 분리

## 테이블 구성 한눈에

| 계층 | 테이블 | 역할 |
|---|---|---|
| 유저 | `user` · `user_profile` · `user_avoided_muscle` · `body_weight_log` | 계정(소셜), 프로필(1:1), 기피 부위(N:M), 체중 추이 |
| 운동 마스터 | `exercise` · `equipment` · `muscle` · `exercise_muscle` | 종목 206종(slug unique) · 장비·근육 마스터 · 주동/보조근 매핑 |
| 템플릿(계획) | `routine` · `routine_exercise` · `routine_set` | 루틴 → 운동(순서) → 세트(기본 무게·목표 렙). `is_ai_generated` 플래그 |
| 로그(완료 기록) | `workout` · `workout_exercise` · `workout_set` | 세션 → 수행 운동 → 세트(실측 kg·렙·수행/휴식 초) |

## ERD (DBML 원문)

```dbml
Project fitset_app_01_04 {
  database_type: 'MySQL'
  Note: '앱 01~04 요구사항 기반 ERD (id: BINARY(16) / UUIDv7 권장). 진행 중 세션은 active_workout* 계층, 완료 기록은 workout* 계층으로 분리. 순서 컬럼은 order_index(0-based)로 통일.'
}

Enum muscle_role {
  primary   [note: '주동근']
  secondary [note: '보조근']
}

Enum difficulty_level {
  beginner     [note: '초급']
  intermediate [note: '중급']
  advanced     [note: '고급']
}

Enum gender {
  male [note: '남']
  female [note: '여']
}

Enum workout_goal {
  hypertrophy [note: '근비대']
  strength    [note: '근력']
  weight_loss [note: '체중감량']
  endurance   [note: '체력유지']
}

// ── 유저 ─────────────────────────────────────────────

Table user {
  user_id          binary(16)   [pk]
  provider         varchar(255) [not null, note: 'kakao·apple']
  provider_user_id varchar(255) [not null]
  email            varchar(255) [not null, unique, note: '계정 이메일(변경 불가)']
  created_at       datetime     [not null, default: `now()`]
  updated_at       datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
  deleted_at       datetime     [note: 'NULL=활성, 값=탈퇴 시점']

  indexes {
    (provider, provider_user_id) [unique]
  }
}

Table user_profile {
  user_profile_id   binary(16)    [pk]
  user_id           binary(16)    [not null, ref: - user.user_id, note: '1:1']
  nickname          varchar(255)  [not null]
  profile_image_url varchar(500)  [not null]
  gender            gender        [not null]
  birth_date        date          [not null]
  height_cm         decimal(5,1)  [not null]
  workout_goal      workout_goal  [note: '운동 목적']
  level             difficulty_level [note: '사용자 수준']
  created_at        datetime      [not null, default: `now()`]
  updated_at        datetime      [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']

  indexes {
    user_id [unique]  // FK UNIQUE로 1:1 강제
  }
}

Table user_avoided_muscle {
  user_avoided_muscle_id binary(16) [pk]
  user_id                binary(16) [not null, ref: > user.user_id]
  muscle_id              binary(16) [not null, ref: > muscle.muscle_id]
  created_at             datetime   [not null, default: `now()`]
  updated_at             datetime   [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']

  indexes {
    (user_id, muscle_id) [unique]
  }
}

// ── 운동 마스터 ───────────────────────────────────────

Table exercise {
  exercise_id   binary(16)       [pk]
  slug          varchar(255)     [unique, not null, note: 'URL·식별용']
  name          varchar(255)     [unique, not null]
  equipment_id  binary(16)       [not null, ref: > equipment.equipment_id, note: '맨몸운동도 장비 레코드로 존재']
  difficulty    difficulty_level [not null, note: '난이도']
  instructions  json             [not null, note: '운동 수행 방법']
  thumbnail_url varchar(500)     [not null, note: '썸네일']
  video_url     varchar(500)     [not null, note: '수행 영상']
  created_at    datetime         [not null, default: `now()`]
  updated_at    datetime         [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

// ── 근육 그룹 · 장비 (마스터 + 다대일/다대다) ────────────────────

Table equipment {
  equipment_id binary(16)   [pk]
  name         varchar(255) [unique, not null, note: '맨몸·바벨·덤벨·머신·케이블·케틀벨·밴드']
  thumbnail_url varchar(500)     [not null, note: '썸네일']
  created_at   datetime     [not null, default: `now()`]
  updated_at   datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table muscle {
  muscle_id  binary(16)   [pk]
  name       varchar(255) [unique, not null, note: '가슴·등·어깨·팔·다리·코어 등']
  thumbnail_url varchar(500)     [not null, note: '썸네일']
  created_at datetime     [not null, default: `now()`]
  updated_at datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table exercise_muscle {
  exercise_muscle_id binary(16)  [pk]
  exercise_id        binary(16)  [not null, ref: > exercise.exercise_id]
  muscle_id          binary(16)  [not null, ref: > muscle.muscle_id]
  role               muscle_role [not null, note: 'primary(주)·secondary(보조)']
  created_at         datetime    [not null, default: `now()`]
  updated_at         datetime    [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']

  indexes {
    (exercise_id, muscle_id) [unique, name: 'uq_exercise_muscle']
  }
}

// ── 템플릿 계층 (운동 계획) ──────────────────────────────

Table routine {
  routine_id      binary(16)   [pk]
  user_id         binary(16)   [not null, ref: > user.user_id]
  name            varchar(255) [not null]
  is_ai_generated boolean      [not null, default: false, note: 'AI로 생성된 루틴 여부']
  created_at      datetime     [not null, default: `now()`]
  updated_at      datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
  deleted_at      datetime     [note: 'NULL=활성']
}

Table routine_exercise {
  routine_exercise_id binary(16) [pk]
  routine_id          binary(16) [not null, ref: > routine.routine_id]
  exercise_id         binary(16) [not null, ref: > exercise.exercise_id]
  order_index         int        [not null, note: '루틴 내 운동 순서(0-based)']
  created_at          datetime   [not null, default: `now()`]
  updated_at          datetime   [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table routine_set {
  routine_set_id      binary(16)   [pk]
  routine_exercise_id binary(16)   [not null, ref: > routine_exercise.routine_exercise_id]
  order_index         int          [not null, note: '세트 순서(0-based)']
  default_weight_kg   decimal(6,2)
  target_reps         int
  created_at          datetime     [not null, default: `now()`]
  updated_at          datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

// ── 로그 계층 (완료된 수행 기록) ─────────────────────────

Table workout {
  workout_id binary(16) [pk]
  user_id    binary(16) [not null, ref: > user.user_id]
  routine_id binary(16) [ref: > routine.routine_id, note: '시작한 루틴(빈 운동이면 NULL)']
  started_at datetime   [not null]
  ended_at   datetime   [not null]
  active_duration_seconds unsigned_int [not null]
  created_at datetime   [not null, default: `now()`]
  updated_at datetime   [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table workout_exercise {
  workout_exercise_id binary(16) [pk]
  workout_id          binary(16) [not null, ref: > workout.workout_id]
  exercise_id         binary(16) [not null, ref: > exercise.exercise_id]
  order_index         int        [not null, note: '운동 내 수행 순서(0-based)']
  created_at          datetime   [not null, default: `now()`]
  updated_at          datetime   [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table workout_set {
  workout_set_id      binary(16)   [pk]
  workout_exercise_id binary(16)   [not null, ref: > workout_exercise.workout_exercise_id]

  order_index         int          [not null, note: '세트 순서(0-based)']

  duration_sec        int          [not null, default: `0`, note: '세트 수행 시간(초)']
  rest_sec            int          [not null, default: `0`, note: '다음 세트까지 휴식 시간(초)']

  weight_kg           decimal(6,2) [not null, default: `0`, note: '실제 수행 kg']
  reps                int          [not null, default: `0`, note: '실제 수행 횟수']

  created_at          datetime     [not null, default: `now()`]
  updated_at          datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}

Table body_weight_log {
  body_weight_log_id binary(16)   [pk]
  user_id            binary(16)   [not null, ref: > user.user_id]
  weight_kg          decimal(5,2) [not null]
  measured_at        datetime         [not null, note: '측정일']
  created_at         datetime     [not null, default: `now()`]
  updated_at         datetime     [not null, note: 'ON UPDATE CURRENT_TIMESTAMP']
}
```

## AI 서버 관점 정합성 체크 (2026-07-25 최초 · 2026-07-29 ERD 갱신 반영)

우리 문서(내부 API 명세·document-structure)와 맞춰 본 결과. ✅ = 정합, ⚠️ = 협의·확인 필요.

| # | 항목 | 판정 |
|---|---|---|
| 1 | `exercise.slug` unique not null | ✅ 종목 식별자 = slug 확정과 정합. 내부 API `GET /internal/exercises/{slug}` 조회 가능. PK는 `exercise_id`(BINARY 16)지만 내부 API 와이어에는 노출 안 됨 |
| 2 | `exercise.thumbnail_url` not null | ✅ 항상 존재 — 내부 API §4.3의 `thumbnailUrl` Null 허용(Y)은 안전 마진일 뿐. `video_url`도 있어 exerciseGif 흐름 지원 |
| 3 | `workout_set.duration_sec`·`rest_sec` 존재 | ✅ 내부 API 협의 포인트 ⑤(세트 스키마 확장 요청)가 스키마에 반영됨. **단 백엔드는 `NOT NULL default 0`, 내부 API 명세는 `null` 허용** — "미기록 = 0"인지 "0초 수행"인지 의미 구분 필요. 0을 미기록으로 간주하면 내부 API 응답에서 null 변환 권장 (⚠️) |
| 4 | `workout_set.weight_kg NOT NULL default 0` | ⚠️ 맨몸 운동 무게 표현이 `null`이 아닌 `0` — 내부 API·AI 무게 추천 로직에서 0=맨몸/미기록 처리 규칙 합의 필요 (협의 포인트 ⑤와 동일 맥락) |
| 5 | `user.provider` note `kakao·apple` | ⚠️ 확정된 소셜 로그인은 **카카오·구글** — note 표기 수정 필요 (01 규약 때부터 이어진 불일치) |
| 6 | `workout_goal.weight_loss` (snake_case) | ⚠️ 팀 enum 카탈로그 key는 `weightLoss`(camelCase) — DB 내부 표기와 API 와이어 표기의 변환 계층 합의 필요 (MUSCLE·EQUIP은 name 컬럼이라 무관) |
| 7 | `gender` `male`/`female` (소문자) | ⚠️ 내부 API 명세엔 `MALE`/`FEMALE`로 예시됨 (협의 포인트 ② 미확정) — 백엔드 소문자 기준이면 명세 쪽을 맞추면 됨 |
| 8 | `muscle.name` note "가슴·등·어깨·팔·다리·코어 등" | ⚠️ 팀 MUSCLE enum은 12종(팔·다리 없음, 이두근·대퇴사두근 등 세분) — 마스터 시드 시 12종·한글 라벨로 넣는지 확인 필요 |
| 9 | `routine.is_ai_generated` | ✅ AI 생성 루틴 저장 흐름(클라 → 백엔드 `POST /routines`) 구분 지원 |
| 10 | `user_profile.workout_goal`·`level` nullable | ✅ 내부 API §4.1의 Null 허용(미입력 시 null, 기본값 처리는 AI 서버 책임)과 정합 |
| 11 | `body_weight_log` 추이 테이블 | ✅ §4.1 프로필은 **최신 1건**만 반환. 추이 전체는 2026-07-29 신설한 내부 API §4.4 `GET /internal/users/{userId}/body-weights`로 조회 — 챗봇 체중 변화 차트용 |
| 12 | 순서 컬럼 `order_index` 0-based 통일 (2026-07-29 갱신) | ✅ `routine_exercise`·`routine_set`·`workout_exercise`·`workout_set` 4개 테이블이 모두 `order_index`(0-based)로 통일됨. 기존 `position`·`set_number`(1-based) 혼재가 해소되어 **내부 API·클라이언트 API의 `orderIndex`(0-based)와 변환 없이 1:1 대응**한다. 이전 명세의 "세트 번호 1,2,3..." 기준 코드가 있다면 오프셋 제거 필요 |
| 13 | `workout.active_duration_seconds` | ✅ 순수 운동 시간(휴식·이탈 제외) 원본 보유 — 챗봇 운동 시간 차트를 백엔드 집계 없이 그릴 수 있다. 내부 API §4.6에서 `endedAt − startedAt`(경과)과 함께 그대로 노출 |
| 14 | `exercise.instructions` json · `video_url` | ✅ 운동 가이드(`responseScheme=exerciseGif`) 응답의 수행 방법·영상 출처. 단 내부 API §4.3 응답에는 아직 두 필드가 없어 **추가 협의 필요** (⚠️ 협의 포인트 ⑪) |
