# 백엔드(Spring) DB ERD — 참조 문서

> 2026-08-25 백엔드 리팩토링(#92·#93·#95·#96·#98·#99) 공유본(fitset_app_01_04)으로 본문 교체 완료, AI 서버 엔티티도 반영 완료. 요지: 단위 접미사 제거(height_cm→height 등), body_weight_log→body_weight_history·routine\*→workout_template\*·workout\*→workout_history\* 개명, active_duration_seconds→pause_seconds(순수 운동 시간은 경과-일시정지로 유도), slug 컬럼 삭제(thumbnail_key 자연키 대체 — AI 서버는 키 파일명에서 slug를 계산 컬럼으로 유도).

백엔드 스프링 서버가 보유한 MySQL ERD (2026-08-25 백엔드 공유본 — 구현 스키마 `schema.sql` + JPA 엔티티 기준).
**AI 서버는 이 DB를 직접 조회한다** (2026-08-12 내부 API 파기 합의) — 읽기 전용 계정 `fitset_readonly`, 코드 사본은 각 도메인 패키지 `domain.py`(SQLAlchemy 엔티티, 공유 베이스 core/orm). 구 [내부 API 명세](백엔드%20내부%20API%20명세.md)는 폐기.
이 문서는 직조회 계약·데이터 정합성 검토를 위한 **참조용 사본**이며, 정본은 백엔드 리포.

- DB: MySQL, PK는 전 테이블 `id` `BINARY(16)` UUID (Hibernate `@UuidGenerator` TIME)
- enum은 MySQL 네이티브 ENUM, 값은 대문자 (Hibernate `EnumType.STRING`)
- 시간 컬럼은 `datetime(6)`, `ON UPDATE CURRENT_TIMESTAMP` 미사용 (Hibernate `updated_at`을 DB가 덮어쓰지 않게)
- 완료된 운동 기록은 `workout_history*` 계층, 운동 템플릿은 `workout_template*` 계층

## 테이블 구성 한눈에

| 계층 | 테이블 | 역할 |
|---|---|---|
| 유저 | `users` · `user_profile` · `user_avoided_muscle` · `refresh_token` · `body_weight_history` | 계정(소셜), 프로필(1:1), 기피 부위(N:M), 리프레시 토큰(유저당 1건), 체중 추이 |
| 운동 마스터 | `exercise` · `equipment` · `muscle` · `exercise_muscle` | 종목(thumbnail_key 자연키) · 장비·근육 마스터(thumbnail_key·description) · 주동/보조근 매핑(role 포함 unique) |
| 템플릿(계획) | `workout_template` · `workout_template_exercise` · `workout_template_set` | 루틴 → 운동(순서) → 세트(기본 무게·목표 렙·목표 시간). `is_ai_generated` 플래그 |
| 로그(완료 기록) | `workout_history` · `workout_history_exercise` · `workout_history_set` | 세션 → 수행 운동 → 세트(실측 kg·렙·수행/휴식 초). `pause_seconds`로 순수 운동 시간 유도 |
| 소셜 | `user_follow` · `feed` · `feed_media` · `feed_like` · `feed_comment` · `feed_comment_like` | 팔로우 그래프, 피드(운동 기록 공유 가능), 미디어, 좋아요, 댓글, 댓글 좋아요 |

친구 추천은 별도 영속 데이터 없이 `user_follow`·`user_profile` 기반 조회 로직으로 계산한다.

## ERD (DBML 원문)

```dbml
Project fitset_app_01_04 {
  database_type: 'MySQL'
  Note: '현재 구현된 스키마(src/main/resources/db/schema.sql + JPA 엔티티) 기준 ERD. 식별자는 BINARY(16) UUID(Hibernate @UuidGenerator TIME), PK 컬럼명은 전 테이블 id. 완료된 운동 기록은 workout_history 계층, 운동 템플릿은 workout_template 계층. 순서 컬럼은 order_index(0-based), 시간 컬럼은 *_seconds(int). enum 은 MySQL 네이티브 ENUM 이며 값은 대문자(Hibernate EnumType.STRING). 문자열은 utf8mb4_unicode_ci. created_at/updated_at 은 BaseTimeEntity 가 값을 채우며, DB DEFAULT 는 컬럼을 명시하지 않는 seed INSERT 를 위한 보루다. ON UPDATE CURRENT_TIMESTAMP 는 쓰지 않는다(Hibernate 가 쓴 updated_at 을 DB 가 덮어써 값이 어긋난다). 성능용 보조 인덱스는 이 문서에서 다루지 않고 유니크 제약만 표기한다.'
}

Enum muscle_role {
  PRIMARY   [note: '주동근']
  SECONDARY [note: '보조근']
}

Enum difficulty_level {
  BEGINNER     [note: '초급']
  INTERMEDIATE [note: '중급']
  ADVANCED     [note: '고급']
}

Enum exercise_type {
  WEIGHT_AND_REPS [note: '무게 × 횟수 (예: 벤치프레스, 밴드 컬)']
  REPS_ONLY       [note: '횟수만 (예: 푸쉬업, 풀업)']
  DURATION        [note: '시간 (예: 플랭크, 트레드밀)']
}

Enum gender {
  MALE   [note: '남']
  FEMALE [note: '여']
}

Enum workout_goal {
  HYPERTROPHY [note: '근비대']
  STRENGTH    [note: '근력']
  WEIGHT_LOSS [note: '체중감량']
  ENDURANCE   [note: '체력유지']
}

// ── 유저 ─────────────────────────────────────────────

Table users {
  id               binary(16)   [pk]
  provider         varchar(20)  [not null, note: 'KAKAO·GOOGLE (AuthProvider)']
  provider_user_id varchar(255) [not null]
  email            varchar(255) [not null]
  created_at       datetime(6)  [not null, default: `now(6)`]
  updated_at       datetime(6)  [not null, default: `now(6)`]

  indexes {
    (provider, provider_user_id) [unique, name: 'uk_users_provider_identity']
  }
}

Table user_profile {
  id                binary(16)       [pk]
  user_id           binary(16)       [not null, ref: - users.id, note: '1:1']
  nickname          varchar(255)     [not null]
  profile_image_url varchar(500)     [not null]
  gender            gender           [not null]
  birth_date        date             [not null]
  height            decimal(5,1)     [not null]
  workout_goal      workout_goal     [not null, note: '운동 목적']
  level             difficulty_level [not null, note: '사용자 수준']
  created_at        datetime(6)      [not null, default: `now(6)`]
  updated_at        datetime(6)      [not null, default: `now(6)`]

  indexes {
    user_id [unique, name: 'uk_user_profile_user']  // FK UNIQUE 로 1:1 강제
  }
}

Table user_avoided_muscle {
  id         binary(16)  [pk]
  user_id    binary(16)  [not null, ref: > users.id]
  muscle_id  binary(16)  [not null, ref: > muscle.id]
  created_at datetime(6) [not null, default: `now(6)`]
  updated_at datetime(6) [not null, default: `now(6)`]

  indexes {
    (user_id, muscle_id) [unique, name: 'uk_user_avoided_muscle']
  }
}

Table refresh_token {
  id          binary(16)   [pk]
  user_id     binary(16)   [not null, ref: - users.id, note: '유저당 1건(재발급 시 교체)']
  token_value varchar(255) [not null]
  expires_at  datetime(6)  [not null]
  created_at  datetime(6)  [not null, default: `now(6)`]
  updated_at  datetime(6)  [not null, default: `now(6)`]

  indexes {
    user_id [unique, name: 'uk_refresh_token_user']
  }
}

Table body_weight_history {
  id          binary(16)    [pk]
  user_id     binary(16)    [not null, ref: > users.id]
  weight      decimal(5,2)  [not null]
  measured_at datetime(6)   [not null, note: '측정 시각. 하루 여러 건 허용(유니크 제약 없음)']
  created_at  datetime(6)   [not null, default: `now(6)`]
  updated_at  datetime(6)   [not null, default: `now(6)`]
}

// ── 운동 마스터 ───────────────────────────────────────

Table exercise {
  id            binary(16)       [pk]
  name          varchar(255)     [not null]
  thumbnail_key varchar(255)     [unique, not null, note: '썸네일 객체 키. 종목 식별용 자연키를 겸한다']
  video_key     varchar(255)     [not null, note: '시연 영상 객체 키']
  equipment_id  binary(16)       [not null, ref: > equipment.id, note: '맨몸운동도 장비 레코드로 존재']
  difficulty    difficulty_level [not null, note: '난이도']
  exercise_type exercise_type    [not null, note: '기록 방식(무게·횟수·시간)']
  instructions  json             [not null, note: '운동 수행 방법']
  created_at    datetime(6)      [not null, default: `now(6)`]
  updated_at    datetime(6)      [not null, default: `now(6)`]
}

// ── 근육 그룹 · 장비 (마스터 + 다대일/다대다) ────────────────────

Table equipment {
  id            binary(16)   [pk]
  thumbnail_key varchar(255) [unique, not null, note: '썸네일 객체 키. 식별용 자연키를 겸한다']
  name          varchar(255) [not null, note: '맨몸·바벨·덤벨·머신·케이블·케틀벨·밴드']
  description   varchar(255) [not null, note: '한 줄 설명']
  created_at    datetime(6)  [not null, default: `now(6)`]
  updated_at    datetime(6)  [not null, default: `now(6)`]
}

Table muscle {
  id            binary(16)   [pk]
  thumbnail_key varchar(255) [unique, not null, note: '썸네일 객체 키. 식별용 자연키를 겸한다']
  name          varchar(255) [not null, note: '가슴·등·어깨·팔·다리·코어 등']
  description   varchar(255) [not null, note: '한 줄 설명']
  created_at    datetime(6)  [not null, default: `now(6)`]
  updated_at    datetime(6)  [not null, default: `now(6)`]
}

Table exercise_muscle {
  id          binary(16)  [pk]
  exercise_id binary(16)  [not null, ref: > exercise.id]
  muscle_id   binary(16)  [not null, ref: > muscle.id]
  role        muscle_role [not null, note: 'PRIMARY(주)·SECONDARY(보조)']
  created_at  datetime(6) [not null, default: `now(6)`]
  updated_at  datetime(6) [not null, default: `now(6)`]

  indexes {
    (exercise_id, muscle_id, role) [unique, name: 'uk_exercise_muscle']  // 시드를 멱등하게 재실행하기 위한 자연키
  }
}

// ── 템플릿 계층 (운동 계획) ──────────────────────────────

Table workout_template {
  id              binary(16)   [pk]
  user_id         binary(16)   [not null, ref: > users.id]
  name            varchar(255) [not null]
  is_ai_generated boolean      [not null, note: 'AI 로 생성된 루틴 여부. MySQL BIT(1)']
  created_at      datetime(6)  [not null, default: `now(6)`]
  updated_at      datetime(6)  [not null, default: `now(6)`]
}

Table workout_template_exercise {
  id          binary(16)  [pk]
  workout_template_id  binary(16)  [not null, ref: > workout_template.id]
  exercise_id binary(16)  [not null, ref: > exercise.id, note: 'DB FK 는 있으나 엔티티는 UUID 값으로만 참조']
  order_index int         [not null, note: '루틴 내 운동 순서(0-based)']
  created_at  datetime(6) [not null, default: `now(6)`]
  updated_at  datetime(6) [not null, default: `now(6)`]
}

Table workout_template_set {
  id                      binary(16)   [pk]
  workout_template_exercise_id     binary(16)   [not null, ref: > workout_template_exercise.id]
  order_index             int          [not null, note: '세트 순서(0-based)']
  default_weight          decimal(6,2) [not null, default: 0, note: '0=미지정']
  target_reps             int          [not null, default: 0, note: '0=미지정']
  target_duration_seconds int          [not null, default: 0, note: '시간형 종목 목표 시간(초). 0=미지정']
  created_at              datetime(6)  [not null, default: `now(6)`]
  updated_at              datetime(6)  [not null, default: `now(6)`]

  Note: '어느 값이 유효한지는 조회 시점에 exercise_type 으로 판단한다. 타입별 필수값을 컬럼 제약으로 강제하지 않는다.'
}

// ── 로그 계층 (완료된 수행 기록) ─────────────────────────

Table workout_history {
  id                      binary(16)  [pk]
  user_id                 binary(16)  [not null, ref: > users.id]
  workout_template_id              binary(16)  [ref: > workout_template.id, note: '시작한 루틴(빈 운동이면 NULL). ON DELETE SET NULL — 루틴이 삭제돼도 세션 기록은 남는다']
  started_at              datetime(6) [not null]
  ended_at                datetime(6) [not null]
  pause_seconds           int         [not null, note: '일시정지 시간(초). 실제 운동 시간은 (ended_at - started_at) - pause_seconds 로 유도']
  created_at              datetime(6) [not null, default: `now(6)`]
  updated_at              datetime(6) [not null, default: `now(6)`]
}

Table workout_history_exercise {
  id          binary(16)  [pk]
  workout_history_id  binary(16)  [not null, ref: > workout_history.id]
  exercise_id binary(16)  [not null, ref: > exercise.id, note: 'DB FK 는 있으나 엔티티는 UUID 값으로만 참조']
  order_index int         [not null, note: '운동 내 수행 순서(0-based)']
  created_at  datetime(6) [not null, default: `now(6)`]
  updated_at  datetime(6) [not null, default: `now(6)`]
}

Table workout_history_set {
  id                  binary(16)   [pk]
  workout_history_exercise_id binary(16)   [not null, ref: > workout_history_exercise.id]

  order_index         int          [not null, note: '세트 순서(0-based)']

  duration_seconds    int          [not null, note: '세트 수행 시간(초) — 항상 기록']
  rest_seconds        int          [not null, note: '다음 세트까지 휴식 시간(초). 0=휴식 없음/마지막 세트']

  weight              decimal(6,2) [not null, note: '실제 수행 kg. 0=맨몸운동']
  reps                int          [not null, note: '실제 수행 횟수 — 항상 기록']

  created_at          datetime(6)  [not null, default: `now(6)`]
  updated_at          datetime(6)  [not null, default: `now(6)`]
}

// ── 소셜: 피드 ────────────────────────────────────────

Table feed {
  id         binary(16) [pk]

  user_id    binary(16) [
    not null,
    ref: > users.id,
    note: '피드 작성자'
  ]

  workout_id binary(16) [
    ref: > workout_history.id,
    note: '공유한 완료 운동 기록. 일반 피드이면 NULL. 운동 기록 삭제 시 NULL 유지 권장'
  ]

  content    text       [
    not null,
    note: '피드 본문'
  ]

  created_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  updated_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  Note: '''
  workout_id 는 선택값이다.

  운동 수행 결과를 공유하는 피드는 workout_id 를 참조하고,
  운동 기록과 관계없는 일반 피드는 NULL 로 둘 수 있다.

  workout 의 실제 수행 수치 데이터를 feed 테이블에 중복 저장하지 않는다.
  필요한 경우 workout 계층과 조합하여 조회한다.
  '''
}


// ── 소셜: 피드 미디어 ─────────────────────────────────

Table feed_media {
  id          binary(16)   [pk]

  feed_id     binary(16)   [
    not null,
    ref: > feed.id,
    note: '미디어가 속한 피드'
  ]

  media_url   varchar(500) [
    not null,
    note: '피드에 첨부된 이미지 또는 미디어 URL'
  ]

  order_index int          [
    not null,
    note: '피드 내 미디어 표시 순서(0-based)'
  ]

  created_at  datetime(6)  [
    not null,
    default: `now(6)`
  ]

  updated_at  datetime(6)  [
    not null,
    default: `now(6)`
  ]

  indexes {
    (feed_id, order_index) [unique, name: 'uk_feed_media_order']
  }

  Note: '''
  하나의 피드에 여러 미디어를 첨부할 수 있다.

  동일한 피드 내에서 order_index 는 중복될 수 없다.
  피드 삭제 시 해당 피드의 미디어도 함께 삭제하는 것을 권장한다.
  '''
}


// ── 소셜: 피드 좋아요 ─────────────────────────────────

Table feed_like {
  id         binary(16) [pk]

  feed_id    binary(16) [
    not null,
    ref: > feed.id,
    note: '좋아요 대상 피드'
  ]

  user_id    binary(16) [
    not null,
    ref: > users.id,
    note: '좋아요를 누른 회원'
  ]

  created_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  updated_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  indexes {
    (feed_id, user_id) [unique, name: 'uk_feed_like']
  }

  Note: '''
  한 회원은 하나의 피드에 최대 한 번만 좋아요할 수 있다.

  (feed_id, user_id) 유니크 제약으로 중복 좋아요를 방지한다.
  '''
}


// ── 소셜: 피드 댓글 ───────────────────────────────────

Table feed_comment {
  id         binary(16) [pk]

  feed_id    binary(16) [
    not null,
    ref: > feed.id,
    note: '댓글이 작성된 피드'
  ]

  user_id    binary(16) [
    not null,
    ref: > users.id,
    note: '댓글 작성자'
  ]

  content    text       [
    not null,
    note: '댓글 내용'
  ]

  created_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  updated_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  Note: '''
  현재 요구사항에서는 단일 레벨 댓글만 지원한다.

  대댓글이 필요해지면 parent_comment_id 를 nullable 자기참조 FK 로
  추가하는 방식으로 확장할 수 있다.
  '''
}


// ── 소셜: 댓글 좋아요 ─────────────────────────────────

Table feed_comment_like {
  id         binary(16) [pk]

  comment_id binary(16) [
    not null,
    ref: > feed_comment.id,
    note: '좋아요 대상 댓글'
  ]

  user_id    binary(16) [
    not null,
    ref: > users.id,
    note: '좋아요를 누른 회원'
  ]

  created_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  updated_at datetime(6) [
    not null,
    default: `now(6)`
  ]

  indexes {
    (comment_id, user_id) [unique, name: 'uk_feed_comment_like']
  }

  Note: '''
  한 회원은 하나의 댓글에 최대 한 번만 좋아요할 수 있다.

  (comment_id, user_id) 유니크 제약으로 중복 좋아요를 방지한다.
  '''
}
```

## 2026-08-12 공유본에서 달라진 것 (2026-07-29본 대비)

1. 테이블명 `user` → `users`, PK 컬럼명이 전 테이블 `id`로 통일 (기존 `user_id`·`exercise_id` 등 테이블별 이름 폐기). 내부 API 와이어에는 PK가 노출되지 않아 AI 서버 영향 없음.
2. enum 값이 소문자에서 대문자로 확정 (`MALE`·`WEIGHT_LOSS` 등, Hibernate EnumType.STRING).
3. `exercise.exercise_type` 신설 (WEIGHT_AND_REPS·REPS_ONLY·DURATION) — exercise_catalog가 캐시하는 `exerciseType`의 DB 정본.
4. `exercise.thumbnail_url`·`video_url`, `equipment`·`muscle`의 `thumbnail_url` 컬럼 제거. `equipment`·`muscle`에 `slug` 신설.
5. `refresh_token` 테이블 추가 (유저당 1건, 재발급 시 교체).
6. `routine_set.target_duration_seconds` 추가, `default_weight_kg`·`target_reps`가 nullable에서 `NOT NULL default 0`(0=미지정)으로 변경.
7. `users.deleted_at`·`routine.deleted_at` 제거, `users.email` unique 제거, `ON UPDATE CURRENT_TIMESTAMP` 폐기(Hibernate가 updated_at 소유).
8. `exercise_muscle` unique가 (exercise_id, muscle_id)에서 role 포함 3컬럼으로 변경.
9. 소셜 계층 6테이블 신설 — `user_follow`·`feed`·`feed_media`·`feed_like`·`feed_comment`·`feed_comment_like`.
10. `user_profile.workout_goal`·`level`이 nullable에서 `NOT NULL`로 변경.

## AI 서버 관점 정합성 체크 (2026-07-25 최초 · 2026-08-12 공유본 반영)

우리 문서와 맞춰 본 결과. ✅ = 정합, ⚠️ = 협의·확인 필요.
표의 "내부 API §4.x" 참조는 폐기된 구 계약 기준 판정이다 — 내부 API 파기(2026-08-12)로 해당 항목들은 직조회 쿼리 설계 시의 참고 자료가 된다 (null 규정·표기 변환처럼 API가 흡수하던 책임이 전부 AI 서버 쿼리·코드로 넘어옴).

| # | 항목 | 판정 |
|---|---|---|
| 1 | `exercise.slug` unique not null | ✅ 종목 식별자 = slug 확정과 정합. PK 컬럼명이 `id`로 바뀌었지만 내부 API 와이어에 노출되지 않아 영향 없음 |
| 2 | `exercise.exercise_type` 신설 | ✅ 일 1회 배치가 캐시하는 `exerciseType`의 DB 정본 확인. 값 표기는 DB 대문자(WEIGHT_AND_REPS) ↔ 공개 API camelCase 매핑 확인 필요 |
| 3 | `exercise.thumbnail_url`·`video_url` 컬럼 제거 | ⚠️ 공개 API `GET /api/v1/exercises`가 주는 CDN 썸네일·영상 URL의 출처가 DB 컬럼이 아니게 됨 — 백엔드가 slug 기반으로 CDN 경로를 조립하는 것으로 추정. 내부 API §4.3 `videoUrl` 폴백과 exercise_catalog 캐시가 계속 유효한지 확인 필요 |
| 4 | `workout_set` 0의 의미 명문화 | ✅ `weight_kg` 0=맨몸운동, `rest_seconds` 0=휴식 없음/마지막 세트, `duration_seconds`·`reps` 항상 기록 — 구 ERD의 "0이 미기록인지 0값인지" 모호함이 note로 해소. 무게 추천(e1RM)의 0kg 세트 제외 규칙과 정합 |
| 5 | `users.provider` note `kakao·apple` | ⚠️ 확정된 소셜 로그인은 **카카오·구글** — note 표기 불일치 유지 중 (01 규약 때부터) |
| 6 | enum 대문자 (`WEIGHT_LOSS` 등) | ⚠️ 팀 enum 카탈로그 key는 camelCase(`weightLoss`) — DB 표기와 API 와이어 표기의 변환 계층 필요(내부 API §4.1은 camelCase로 응답 중이면 무관). `gender`는 내부 API 예시(MALE/FEMALE)와 일치해 구 ⚠️ 해소 (✅) |
| 7 | `muscle.slug`·`equipment.slug` 신설 | ✅ 팀 MUSCLE(12종)·EQUIP(7종) enum key와 매핑할 식별자가 생김. 단 `muscle.name` note가 여전히 "팔·다리" 등 비세분 표기라 시드가 12종·한글 라벨인지 확인 필요 (⚠️) |
| 8 | `routine.is_ai_generated` | ✅ AI 생성 루틴 저장 흐름(클라 → 백엔드 `POST /routines`) 구분 지원 |
| 9 | `user_profile.workout_goal`·`level` NOT NULL로 변경 | ⚠️ 내부 API §4.1은 null 허용(기본값 처리는 AI 서버 책임 — goal null이면 hypertrophy)이었음. DB가 not null이면 null은 프로필 미생성 케이스뿐인지, §4.1 명세의 null 규정이 유지되는지 확인 필요 |
| 10 | `body_weight_log.measured_at` 하루 여러 건 허용 | ✅ §4.4 체중 추이 조회와 정합 — 차트 집계(일 단위 버킷)는 AI 서버가 수행 |
| 11 | 순서 컬럼 `order_index` 0-based 통일 | ✅ 유지 — 내부 API·클라이언트 API의 `orderIndex`(0-based)와 변환 없이 1:1 대응 |
| 12 | `workout.active_duration_seconds` | ✅ 유지 — 챗봇 운동 시간 차트를 백엔드 집계 없이 그릴 수 있다 (§4.6) |
| 13 | `refresh_token` 테이블 (불투명 토큰, 유저당 1건) | ✅ JWT 규약과 정합 — refresh는 JWT가 아닌 랜덤 문자열이라 access와 혼동될 경로가 없다는 검증 규칙(type 클레임 검사 없음)의 DB 근거 |
| 14 | `routine_set.target_duration_seconds` 신설 | ⚠️ AI 루틴 생성 응답(§4.1)의 세트 스키마는 weight·reps 기반 — DURATION 종목이 추천 루틴에 포함될 때 목표 시간 표현을 클라·백엔드 저장 API와 협의 필요 |
| 15 | `exercise.instructions` json | ✅ 존재 유지 — 운동 가이드(exerciseGif) 수행 방법 출처. 내부 API §4.3 노출 협의(⑪)는 계속 유효 |
| 16 | 소셜 계층 신설 | 참고 — 현 AI 서버 범위(챗봇·루틴 추천)와 무관. 추후 피드 추천 과제 착수 시 `user_follow`·`feed` 조회용 내부 API 신설 협의 필요 (친구 추천은 백엔드 조회 로직 담당으로 명시됨) |
| 17 | `users.deleted_at` 제거 | 참고 — 탈퇴 표현이 소프트 삭제에서 다른 방식(하드 삭제 추정)으로 변경. AI 서버는 탈퇴 유저의 DynamoDB 데이터(스레드·요약) 정리 트리거가 없다는 기존 상태 그대로 — 탈퇴 이벤트 연동은 미해결 과제 |
