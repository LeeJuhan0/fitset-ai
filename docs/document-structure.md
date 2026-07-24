# AI 챗봇 데이터 설계 — ERD & JSON 규약

저장소: **MongoDB** (chat_threads, chat_messages, user_summaries, routines). 루틴 원본은 S3, 배치로 적재.
벡터 검색은 제거 — 루틴 추천은 **룰 필터 + LLM 리랭킹**.

## 1. ERD (dbdiagram.io / DBML)

```dbml
Table chat_threads {
  _id objectid [pk, note: 'thread_id — 생성 시각 내장']
  user_id uuid [note: '복합 인덱스 {user_id: 1, last_message_at: -1} — 스레드 목록 정렬']
  title varchar [note: '첫 유저 발화 기반 LLM 요약 or 앞 30자']
  summary_text text [note: 'LLM 생성 스레드 압축. 초기 null']
  summary_upto objectid [note: '요약에 반영된 마지막 메시지 _id — 다음 요약 배치 시작점']
  last_message_at datetime [note: 'TTL 인덱스 expireAfterSeconds: 1209600 (14일) — 메시지 수신 시 갱신 = 타이머 리셋']

  Note: '''
  대화 스레드 — MongoDB collection
  - 유저당 최대 5개: 6번째 생성 시 last_message_at 최소(가장 오래 미활동) 스레드 삭제 (LRU)
  - 수명: 마지막 메시지 후 14일 미활동 시 TTL 자동 삭제
  - 요약 갱신: summary_upto 이후 메시지가 n턴 쌓이면
    (기존 summary_text + 신규 메시지) → LLM 재요약, summary_upto 전진
    (응답 경로와 분리 — 백그라운드 처리)
  '''
}

Table user_summaries {
  _id uuid [pk, note: 'user_id — 항상 단건 조회']
  summary_text text [note: 'LLM 생성 유저 요약본 (최근 선호 운동, 목표, 제약)']
  created_at datetime
  updated_at datetime

  Note: '''
  장기 메모리 (유저 요약본) — MongoDB collection
  - 영속, 삭제 없음
  - 매 요청 findOne({_id: user_id})로 읽어 시스템 프롬프트 주입
  - n턴 이후 요약 업데이트 (백그라운드)
  '''
}

Table chat_messages {
  _id objectid [pk, note: '생성 시각 내장, 시간순 정렬 겸용']
  thread_id objectid [note: 'chat_threads 참조. 복합 인덱스 {thread_id: 1, _id: 1} — 대화 로드']
  user_id uuid [note: '소유권 검증용']
  role varchar [note: 'user | assistant']
  content text [note: '텍스트 본문 — 항상 존재, LLM 컨텍스트에 들어가는 부분']
  response_scheme varchar [note: 'text | chart | exercise_gif | routine — payload 존재·구조를 결정']
  payload json [note: '§2 — text: null(항상) / chart: 집계 JSON / exercise_gif: exercise_id / routine: routines.exercises 구조']
  created_at datetime

  Note: '''
  대화 메시지 — MongoDB collection (TTL 없음)
  - LLM 컨텍스트 로드: find({thread_id}, {payload: 0}).sort({_id: -1}).limit(N)
    → 역순 뒤집어 사용. 시스템 프롬프트 = user_summaries + summary_text + 최근 N턴
  - payload는 앱 화면 렌더링 조회에서만 포함
  - 삭제 경로 ①: 5개 초과 시 앱이 스레드 삭제 직후 deleteMany({thread_id}) 즉시 실행
  - 삭제 경로 ②: TTL 만료는 스레드 문서만 삭제 → 고아 메시지는 일 1회 배치 정리
    (배치: 메시지 distinct thread_id → chat_threads 부재분만 deleteMany, ①의 실패 안전망 겸용)
  '''
}

Table routines {
  _id varchar [pk, note: 'slug — S3 원본 매핑, 멱등 upsert 키']
  name varchar [note: '루틴 이름']
  description text [note: '루틴 설명 — LLM 후처리 자연어 랭킹']
  exercises json [note: '운동 목록 — 구조는 §3']

  // ── 룰 필터용 메타데이터 ──
  level varchar [note: 'beginner | intermediate | advanced — 유저 수준 상한']
  equipment json [note: '["dumbbell","bench"] — 필요 장비 ⊆ 유저 보유 장비']
  goal varchar [note: 'strength | hypertrophy | endurance | weightLoss']
  muscle_groups json [note: '["chest","shoulders","triceps"] — 타겟·기피 필터 겸용']
  minutes_per_routine integer [note: '가용 시간 range 필터']

  // ── 관리용 ──
  created_at datetime
  updated_at datetime [note: 'S3 → MongoDB 적재 배치 추적']

  Note: '''
  루틴 문서 — MongoDB collection (원본: S3 / 벡터 검색 제거 → 룰 필터 + LLM 리랭킹)
  - 검색: LLM 조건 추출 → find 필터 → 룰 정렬 → 상위 N만 LLM 리랭킹 후 반환
  - 장비 필터: {equipment: {$not: {$elemMatch: {$nin: 유저보유장비}}}}
  - 인덱스: {level: 1, goal: 1, minutes_per_routine: 1}
  '''
}

Table s3_routines {
  slug varchar [pk, note: 'S3 키에 포함 (예: routines/routine_0042.json)']
  body json [note: '루틴 전문: 운동 목록, 세트/렙, 설명, 장비, exercise_id 참조 등']

  Note: '''
  실제 테이블 아님 — S3 버킷의 JSON 객체 (source of truth)
  - S3 → MongoDB routines 적재 배치가 동기화
  '''
}

Ref: chat_threads._id < chat_messages.thread_id [note: '논리적 1:N (FK 아님 — 코드 규약)']
Ref: routines._id - s3_routines.slug [note: '1:1 — 적재 배치가 동기화 책임']
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
  "exercise_id": "uuid — 종목 마스터 참조",
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
      "exercise_id": "uuid",
      "exercise_name": "덤벨 벤치프레스",
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
    "exercise_id": "uuid — 종목 마스터 참조",
    "exercise_name": "바벨 벤치프레스",
    "order_index": 0,
    "sets": [
      { "order_index": 0, "weight": 60.0, "reps": 12 },
      { "order_index": 1, "weight": 60.0, "reps": 10 }
    ]
  }
]
```

- `weight`: kg 숫자 — 템플릿 기본값, 유저별 무게 추천이 덮어씀. 맨몸·미지정은 `null`
- `reps`: 세트당 반복 횟수

## 4. 전역 규칙

1. payload에는 식별자만 저장 (exercise_id, slug) — URL·미디어는 클라가 조회 시점에 획득
2. 시각은 BSON datetime, `_id`(objectid)의 내장 시각을 정렬 기준으로 겸용
3. enum 어휘 강제는 Pydantic — MongoDB는 검증하지 않는다
4. 클라는 모르는 `response_scheme`/`chart_type` 무시 또는 텍스트 폴백 (전방 호환)
5. 원본(S3)이 진실 — MongoDB `routines`는 파생, 적재 배치가 동기화 책임
