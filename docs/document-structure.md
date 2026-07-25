# AI 챗봇 데이터 설계 — ERD & JSON 규약

저장소: **DynamoDB 온디맨드** (chat_threads, chat_messages, user_summaries, routines) — 2026-07-25 확정.
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
  expires_at number [note: 'DynamoDB TTL 속성 — epoch 초, last_message_at + 14일. 메시지 수신 시 갱신 = 타이머 리셋']

  Note: '''
  대화 스레드 — DynamoDB 테이블 (온디맨드)
  - 스레드 목록: Query(user_id) → 최대 5개라 last_message_at 정렬은 서버 메모리에서 (GSI 불필요)
  - 유저당 최대 5개: 6번째 생성 시 last_message_at 최소(가장 오래 미활동) 스레드 삭제 (LRU)
  - 수명: 마지막 메시지 후 14일 미활동 시 TTL 자동 삭제
    (TTL 삭제는 최대 며칠 지연 가능 → 조회 시 expires_at < now 항목은 만료로 간주해 숨김)
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
  - 삭제 경로 ②: TTL 만료는 스레드 항목만 삭제 → 고아 메시지는 일 1회 배치 정리
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
  "slug": "barbell-bench-press — 종목 마스터 참조",
  "exercise_name": "바벨 벤치프레스"
}
```

- GIF는 payload에 없음 — 클라가 종목 상세에서 획득

### 2.4 `routine`

```json
{
  "slug": "upper-body-dumbbell-30",
  "name": "상체 덤벨 30분",
  "minutes_per_routine": 30,
  "exercises": [
    {
      "slug": "dumbbell-bench-press",
      "exercise_name": "덤벨 벤치프레스",
      "thumbnail_url": "varchar(500)",
      "order_index": 0,
      "sets": [
        { "order_index": 0, "weight": 20.0, "reps": 12 }
      ]
    }
  ]
}
```

- `exercises`는 `routines.exercises`(§3)와 동일 구조
- `slug`: `routines._id` 참조. 영상은 안드로이드 로컬 DB에서 클라가 조회
- 미결: 첫 번째 운동을 루틴 썸네일로 쓴다면 `slug` 불필요할 수 있음

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

1. payload에는 식별자만 저장 (slug) — URL·미디어는 클라가 조회 시점에 획득 (단, routine payload의 `thumbnail_url`은 예외로 스냅샷 포함)
2. 시각은 ISO 8601 문자열, 식별자는 ULID — 사전순 = 시간순이므로 Sort Key 정렬 기준 겸용 (ObjectId 내장 시각 대체)
3. enum 어휘 강제는 Pydantic — DynamoDB는 검증하지 않는다
4. 클라는 모르는 `response_scheme`/`chart_type` 무시 또는 텍스트 폴백 (전방 호환)
5. 원본(S3)이 진실 — DynamoDB `routines`와 인메모리는 파생. 변환 배치가 DynamoDB를, 부팅 Scan이 인메모리를 동기화
