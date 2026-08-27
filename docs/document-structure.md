# AI 챗봇 데이터 설계 — ERD & JSON 규약

저장소: **DynamoDB 온디맨드** (chat_threads, chat_messages, user_summaries, routines, exercise_catalog) — 2026-07-25 확정, 카탈로그는 2026-08-05 추가.
루틴 파이프라인: **S3 원본 → 변환 배치 → DynamoDB `routines`(변환 완료본) → 부팅 시 Scan 전량 인메모리 로드**(~110MB).
룰 필터 검색은 항상 인메모리 — DynamoDB `routines`는 부팅 로드 소스 + slug 단건 폴백(GetItem) 전용.
벡터 검색은 제거 — 루틴 추천은 **룰 필터 + LLM 리랭킹**.

식별자 규약: ObjectId 대신 **ULID** (사전순 = 시간순 → SK 정렬 기준 겸용, "생성 시각 내장" 역할 대체).

## 1. ERD (dbdiagram.io / DBML)

```dbml
Table chat_threads {
  user_id uuid [pk, note: 'Partition Key']
  thread_id varchar [pk, note: 'Sort Key — ULID, 생성 시각 내장']
  title varchar [note: '첫 유저 발화 기반 LLM 요약 or 앞 30자']
  summary_text text [note: 'LLM 생성 스레드 압축. 초기 null']
  summary_upto varchar [note: '요약에 반영된 마지막 메시지 message_id(ULID) — 다음 요약 배치 시작점']
  last_message_at datetime [note: 'ISO 8601 — 메시지 수신 시 갱신']
  expires_at number [note: '메시지 보존 기한 — epoch 초, last_message_at + 14일. 메시지 수신 시 갱신 = 타이머 리셋. 2026-08-15부터 DynamoDB TTL 등록 해제 — 항목 자동 삭제 없음']

  Note: '''
  대화 스레드 — DynamoDB 테이블 (온디맨드)
  - 스레드 목록: Query(user_id) → 최대 10개라 last_message_at 정렬은 서버 메모리에서 (GSI 불필요)
  - 유저당 최대 10개: 정원 도달 시 생성 거부 409 THREAD_QUOTA_EXCEEDED (2026-08-15 LRU 자동삭제 폐기, #40)
  - 수명: 마지막 메시지 후 14일 미활동 시 만료 — 메시지만 삭제(조회 시 지연 삭제), 스레드 항목은
    needsDeletion=true로 목록에 남겨 유저 삭제(DELETE §4)를 유도 (2026-08-15, #40 후속)
    ⚠ 배포 시 chat_threads 테이블의 TTL(expires_at) 설정을 해제해야 한다 — 켜져 있으면
    DynamoDB가 만료 항목을 지워버려 needsDeletion 흐름이 깨진다
  - 요약 갱신: summary_upto 이후 메시지가 n턴 쌓이면
    (기존 summary_text + 신규 메시지) → LLM 재요약, summary_upto 전진
    (응답 경로와 분리 — 백그라운드 처리)
  '''
}

Table user_summaries {
  user_id uuid [pk, note: 'Partition Key (SK 없음) — 항상 GetItem 단건 조회']
  summary_text text [note: 'LLM 생성 유저 요약본 (최근 선호 운동, 목표, 제약)']
  created_at datetime
  updated_at datetime

  Note: '''
  장기 메모리 (유저 요약본) — DynamoDB 테이블 (온디맨드, TTL 없음)
  - 영속, 삭제 없음
  - 매 요청 GetItem(user_id)으로 읽어 시스템 프롬프트 주입
  - n턴 이후 요약 업데이트 (백그라운드)
  '''
}

Table chat_messages {
  thread_id varchar [pk, note: 'Partition Key — chat_threads.thread_id 참조']
  message_id varchar [pk, note: 'Sort Key — ULID, 시간순 정렬 겸용']
  user_id uuid [note: '소유권 검증용']
  role varchar [note: 'user | assistant']
  content text [note: '텍스트 본문 — 항상 존재, LLM 컨텍스트에 들어가는 부분']
  response_scheme varchar [note: 'text | chart | exercise_gif | routine — payload 존재·구조를 결정']
  payload json [note: '§2 — text: null(항상) / chart: 집계 JSON / exercise_gif: slug / routine: routines.exercises 구조']
  created_at datetime

  Note: '''
  대화 메시지 — DynamoDB 테이블 (온디맨드, TTL 없음)
  - LLM 컨텍스트 로드: Query(thread_id, ScanIndexForward=false, Limit=N,
    ProjectionExpression=payload 제외 필드 나열) → 역순 뒤집어 사용.
    시스템 프롬프트 = user_summaries + summary_text + 최근 N턴
  - payload는 앱 화면 렌더링 조회에서만 포함 (전체 필드 Query)
  - 삭제 경로 ①: 스레드 삭제 직후 Query(thread_id) → BatchWriteItem 25개 단위 삭제
  - 삭제 경로 ②: 만료 스레드 메시지는 조회(§4.5) 시 지연 삭제 → 접근 없는 스레드 몫은 일 1회 배치 정리
    (배치: 메시지 테이블 스캔으로 thread_id 수집 → chat_threads 부재분만 삭제, ①의 실패 안전망 겸용)
  '''
}

Table routines {
  slug varchar [pk, note: 'Partition Key (SK 없음) — S3 원본 키와 동일, 변환 배치의 멱등 upsert(PutItem) 키']
  name varchar [note: '루틴 이름']
  description text [note: '루틴 설명 — LLM 후처리 자연어 랭킹']
  exercises json [note: '운동 목록 — 구조는 §3 (변환 완료본: 종목 slug·한글명·thumbnail_url)']

  // ── 룰 필터용 메타데이터 ──
  level varchar [note: 'beginner | intermediate | advanced — 유저 수준 상한']
  equipment json [note: '["dumbbell","barbell"] — EQUIP enum. 필요 장비 ⊆ 유저 보유 장비']
  goal varchar [note: 'strength | hypertrophy | endurance | weightLoss']
  muscle_groups json [note: '["chest","shoulders","triceps"] — MUSCLE enum. 타겟·기피 필터 겸용']
  minutes_per_routine integer [note: '가용 시간 range 필터']

  // ── 임베딩 (랭킹용) ──
  embedding binary [note: '묘사 임베딩 — float32 리틀엔디언 팩(1024d, 4KB). 텍스트 = 한글 종목명 나열 + description']
  embedding_model varchar [note: '버전 태그 — global.cohere.embed-v4:0#1024#float32. 모델 교체 시 배치 재실행 판별']

  // ── 관리용 ──
  created_at datetime
  updated_at datetime [note: '변환 배치(S3 → DynamoDB) 적재 추적']

  Note: '''
  루틴 — DynamoDB 테이블 (온디맨드). S3 원본을 변환 배치가 적재한 **변환 완료본**
  - 서버 부팅 시 Scan으로 전량 인메모리 로드 (~110MB) → 룰 필터 검색은 항상 인메모리
    (LLM 조건 추출 → 인메모리 룰 필터 → 룰 정렬 → 상위 N만 LLM 리랭킹 후 반환)
  - 장비 필터: set(routine.equipment) ⊆ set(유저 보유 장비) — 인메모리 set 연산
  - 인메모리 미스(재적재 후 신규 slug 등) 시 GetItem(slug) 폴백
  - 경계: 다중 조건 필터를 DynamoDB 쿼리로 수행하지 않는다 — 이 테이블 역할은
    부팅 로드 소스 + slug 단건 조회뿐
  - 갱신: 변환 배치가 멱등 upsert → 서버 재로드(배포 or 관리자 트리거), 그 사이는 GetItem 폴백이 커버
  '''
}

Table exercise_catalog {
  slug varchar [pk, note: 'Partition Key (SK 없음) — 종목 마스터 조인 키, 배치 멱등 PutItem 키']
  exercise_id uuid [note: '백엔드 종목 마스터 UUID — 클라 루틴 저장(POST /api/v1/routines)·종목 상세 이동 키']
  exercise_type varchar [note: 'WEIGHT_AND_REPS(166) | REPS_ONLY(32) | DURATION(8) — 세트 구성 분기 키']
  thumbnail_url varchar [note: 'CloudFront CDN 썸네일 — 종전 S3 직접 주소는 403이라 폐기(2026-08-05 실측)']
  video_url varchar [note: 'CloudFront CDN 가이드 영상 — 무서명·무기한. 미보유 종목은 속성 없음(종목 마스터 videoUrl 폴백)']

  Note: '''
  종목 카탈로그 — 백엔드 종목 마스터의 파생 캐시 (온디맨드, TTL 없음, 2026-08-05 신설)
  - 출처: 백엔드 공개 API GET /api/v1/exercises (무인증 200 실측) —
    k8s CronJob(03:00 KST, 같은 이미지에 command만 교체)으로 배치 실행 예정, ECS 시절은 EventBridge Scheduler가 RunTask
    (scripts/sync_exercise_catalog.py — slug ∩ 로컬 metadata 206종 교집합만 적재)
  - 담는 것은 AI 서버가 자체 생성 못 하는 값뿐 — UUID·수행 방식·CDN URL.
    한글명·부위·장비는 repo 동봉 metadata가 정본이라 중복 저장하지 않는다
  - 서버는 부팅 시 Scan 인메모리(206건) — 배치가 갱신해도 재시작 전까지 미반영(F16과 동일)
  - 비어 있어도 서버 정상 — exerciseId null. 구 폴백(내부 API §4.3 videoUrl)은 내부 API 파기(2026-08-12)로 소멸, 카탈로그가 영상 URL 유일 출처(비면 영상 없음)
  '''
}

Table s3_routines {
  slug varchar [pk, note: 'S3 키에 포함 (예: routines/{slug}.json, 버킷 fitset-routines-raw)']
  body json [note: '루틴 전문(캐글 원문 보존): 운동 목록, 세트/렙, 설명, 장비, 종목 slug 참조 등']

  Note: '''
  실제 테이블 아님 — S3 버킷의 JSON 객체 (source of truth)
  - 변환 배치가 alias 매핑·enum 재매핑 적용 후 DynamoDB routines로 적재
    (규약: docs/캐글 루틴 변환.md)
  '''
}

Ref: chat_threads.thread_id < chat_messages.thread_id [note: '논리적 1:N (FK 아님 — 코드 규약)']
Ref: routines.slug - s3_routines.slug [note: '1:1 — 변환 배치가 동기화 책임']
Ref: exercise_catalog.slug - routines.slug [note: '논리적 참조 — 응답 조립 시 slug로 UUID·CDN URL·exerciseType을 붙인다 (FK 아님)']
```

## 2. `payload` JSON 규약 (response_scheme별)

`response_scheme`이 payload의 존재와 구조를 결정한다.

### 2.1 `text`

payload는 항상 `null`. 유저 메시지는 항상 이 형태.

### 2.2 `chart`

```json
{
  "chart_type": "line | bar",
  "title": "최근 4주 가슴 볼륨",
  "x_label": "주차",
  "y_label": "볼륨(kg)",
  "x": ["6/29", "7/6", "7/13", "7/20"],
  "series": [
    { "name": "가슴", "values": [4200, 4650, 4400, 5100] }
  ]
}
```

- 색상·스타일은 클라가 결정
- 미결: `chart_type`도 클라 자율로 두고 제거할지 여부

### 2.3 `exercise_gif`

```json
{
  "exerciseId": "11f18f47-… — 백엔드 마스터 UUID, 카탈로그 미스 시 null",
  "slug": "barbell-bench-press — 종목 마스터 참조",
  "exerciseName": "바벨 벤치프레스",
  "videoUrl": "https://dtcevtkuvdwt9.cloudfront.net/videos/….mp4 — CDN, 무기한",
  "expiresAt": null
}
```

- `videoUrl`은 CDN이라 만료가 없다(`expiresAt`은 항상 null) — 저장된 대화를 나중에 열어도 그대로 재생 (2026-08-05 presign 방식 폐기, 2026-08-06 폴백까지 폐기)
- 카탈로그 미보유·CDN 재생 실패 시 구 재조회 경로(내부 API §4.3 `videoUrl`)는 내부 API 파기(2026-08-12)로 소멸 — 클라 §6-B `fallback=true` 재발급도 카탈로그 캐시에서 답한다 (캐시에도 없으면 영상 없음)
- 영상 없는 종목은 `videoUrl`·`expiresAt` 모두 null

### 2.4 `routine`

```json
{
  "slug": "upper-body-dumbbell-30",
  "name": "상체 덤벨 30분",
  "estimatedMinutes": 30,
  "exercises": [
    {
      "exerciseId": "11f18f47-… — 백엔드 마스터 UUID, 카탈로그 미스 시 null",
      "slug": "dumbbell-bench-press",
      "exerciseName": "덤벨 벤치프레스",
      "thumbnailUrl": "CDN URL (varchar 500)",
      "orderIndex": 0,
      "sets": [
        { "orderIndex": 0, "weight": 20.0, "reps": 12, "durationSeconds": 0 }
      ]
    }
  ]
}
```

- 클라이언트 API §4.1 `data.routine`과 동일 구조 — 추천 시점 스냅샷 (API 응답을 그대로 저장, 와이어 camelCase)
- 세트 3필드는 not null — exerciseType별로 해당 없는 자리는 `0` (WEIGHT_AND_REPS: weight+reps / REPS_ONLY: reps / DURATION: durationSeconds, 2026-08-05)
- `exerciseId`·CDN `thumbnailUrl`은 응답 조립 시 `exercise_catalog`에서 붙인다 — `routines.exercises`(§3) 저장분에는 없다

## 3. `routines.exercises` 구조

```json
[
  {
    "slug": "barbell-bench-press — 종목 마스터 참조 (kebab-case slug)",
    "exercise_name": "바벨 벤치프레스",
    "thumbnail_url": "varchar(500)",
    "order_index": 0,
    "sets": [
      { "order_index": 0, "weight": 60.0, "reps": 12 },
      { "order_index": 1, "weight": 60.0, "reps": 10 }
    ]
  }
]
```

- `slug`: 종목 식별자 — **uuid가 아닌 slug** (2026-07-25 확정, 필드명도 `slug`). `exercise_name`은 한글명(metadata `name_ko`)
- `thumbnail_url`: 종목 썸네일 이미지 URL, 최대 500자
- `weight`: kg 숫자 — 템플릿 기본값, 유저별 무게 추천이 덮어씀. 맨몸·미지정은 `null`
- `reps`: 세트당 반복 횟수

## 4. 전역 규칙

1. payload에는 식별자만 저장 (slug) — 만료가 있는 URL은 저장하지 않는다. **CDN URL(무기한)은 예외로 스냅샷 포함** (routine `thumbnailUrl`, exercise_gif `videoUrl` — 2026-08-05 CDN 전환으로 저장 가능해짐)
2. 시각은 ISO 8601 문자열, 식별자는 ULID — 사전순 = 시간순이므로 Sort Key 정렬 기준 겸용 (ObjectId 내장 시각 대체)
3. enum 어휘 강제는 Pydantic — DynamoDB는 검증하지 않는다
4. 클라는 모르는 `response_scheme`/`chart_type` 무시 또는 텍스트 폴백 (전방 호환)
5. 원본(S3)이 진실 — DynamoDB `routines`와 인메모리는 파생. 변환 배치가 DynamoDB를, 부팅 Scan이 인메모리를 동기화
