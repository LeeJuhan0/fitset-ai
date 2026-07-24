# 클라이언트 API 명세 (AI Routine & Chat)

정본: [09. AI 서비스 클라이언트 API 명세](https://asmhangang.atlassian.net/wiki/spaces/FIT/pages/27230212) (Confluence, 초안 2026-07-24).
이 문서는 서버 구현 관점 요약 — 충돌 시 Confluence가 우선.

## 공통

- Base URL: 환경별 호스트 + `/ai`, 호출 주체는 앱 클라이언트
- 인증: `Authorization: Bearer {accessToken}` — Gateway/ELB가 JWT 검증 후 `X-User-Id` 주입. 클라가 userId를 보내는 API는 없음 (사칭 방지)
- 응답: 팀 규약 `{traceId, data}` / `{traceId, error{code, message, details}}`
- 목록 응답은 `data.items`, `page` 객체 없음 (스레드 최대 5개·메시지 전체 로드라 불필요)
- LLM 포함 API(루틴 생성, 채팅)는 클라 타임아웃 30s 권장
- 와이어는 camelCase (`responseScheme`, `exerciseGif`) — 내부 DB는 snake_case, 표현 계층에서 변환

## API 목록

| Method | Path | 설명 | 성공 |
| --- | --- | --- | --- |
| POST | `/ai/routines/generate` | AI 루틴 생성 (홈 고정 워크로드) | 200 |
| GET | `/ai/threads` | 스레드 목록 (최대 5, 최근 활동순, 만료 제외) | 200 |
| POST | `/ai/threads` | 스레드 생성 (5개 초과 시 최구 활동 스레드 삭제 후 생성) | 201 |
| DELETE | `/ai/threads/{threadId}` | 스레드+소속 메시지 전체 삭제, 복구 불가 | 204 |
| GET | `/ai/threads/{threadId}/messages` | 대화 전체 시간순 조회 (payload 포함) | 200 |
| POST | `/ai/threads/{threadId}/messages` | 메시지 전송 → 챗봇 응답 (MVP 동기 JSON 1회) | 200 |

## 1. POST /ai/routines/generate

홈 "AI 루틴 생성" 화면 입력값만 받는다. 신체 정보·수준·기록은 서버가 내부 API(08)로 조회. 대화 스레드를 생성하지 않는다.

요청:

```json
{
  "goal": "hypertrophy",          // GOAL enum: hypertrophy|strength|weightLoss|endurance (필수)
  "level": "intermediate",        // DIFFICULTY enum: beginner|intermediate|advanced (필수)
  "muscleGroups": ["chest", "shoulders", "triceps"],  // MUSCLE enum, 1~12개 (필수)
  "minutes": 50,                  // 10~180 (필수)
  "context": "어깨 통증 있음",     // 자유 텍스트 최대 200자 (선택) — LLM 조건 추출용
  "includeWarmup": true           // 워밍업 세트 포함 여부 (필수) — 종목별 첫 세트 경량 구성
}
```

응답 `data.routine`:

```json
{
  "slug": "upper-body-dumbbell-50",   // 베이스 루틴 참조 — LLM 재구성 시에도 원본 후보 slug 유지 (nullable)
  "name": "상체 집중 50분",
  "description": "어깨 부담을 피해 가슴·삼두 중심으로 구성했어요.",
  "estimatedMinutes": 50,
  "exercises": [
    {
      "exerciseId": "uuid", "exerciseName": "덤벨 벤치프레스", "orderIndex": 0,
      "sets": [ { "orderIndex": 0, "weight": 12.0, "reps": 15 } ]
    }
  ]
}
```

비즈니스 규칙:

1. 프로필 `avoidBodyParts`는 `muscleGroups`와 무관하게 하드 필터 — 전부 충돌 시 `NO_ROUTINE_CANDIDATE`
2. 세트 `weight`는 최근 기록 기반 무게 추천값, 기록 없으면 신체 정보 기반 초기값. 맨몸은 `null`
3. 파이프라인: 후보 룰 필터 → 룰 점수 정렬 → LLM 최종 구성
4. `exercises[]`는 04 명세 루틴 구조와 호환 — 클라가 그대로 백엔드 `POST /routines`(루틴 저장)에 사용. AI 서버는 쓰기 없음

오류: 400 `INVALID_REQUEST` / 401 / 404 `USER_NOT_FOUND` / 409 `NO_ROUTINE_CANDIDATE` / 503 `AI_UNAVAILABLE`

## 2. GET /ai/threads

```json
{ "items": [ { "threadId": "oid", "title": "어깨 재활 루틴 상담", "lastMessageAt": "2026-07-23T09:12:00Z" } ] }
```

- `title`: 첫 발화 기반 자동 생성, 첫 메시지 전이면 `null`. `lastMessageAt`: 빈 스레드면 `null`

## 3. POST /ai/threads

본문 없음 → `201` `{ "threadId": "oid", "createdAt": "..." }`

- 유저당 활성 스레드 최대 5개, 초과 시 마지막 활동이 가장 오래된 스레드 삭제 후 생성 (협의 포인트 ③: 409 반환 대안 논의 중)

## 4. DELETE /ai/threads/{threadId}

`204 No Content`. 오류: 401 / 403 `THREAD_FORBIDDEN` / 404 `THREAD_NOT_FOUND`

## 5. GET /ai/threads/{threadId}/messages

```json
{
  "items": [
    { "messageId": "oid", "role": "user", "content": "...", "responseScheme": null, "payload": null, "createdAt": "..." },
    { "messageId": "oid", "role": "assistant", "content": "...", "responseScheme": "routine", "payload": { }, "createdAt": "..." }
  ]
}
```

- user 메시지는 `responseScheme`·`payload` 모두 `null`
- 오류: 401 / 403 / 404 (스레드 없음·만료)

## 6. POST /ai/threads/{threadId}/messages

요청: `{ "content": "이 루틴에서 스쿼트 빼줘" }` (1~1000자)

응답:

```json
{
  "message": {
    "messageId": "oid", "role": "assistant", "content": "...",
    "responseScheme": "routine", "payload": { }, "createdAt": "..."
  },
  "threadTitle": "하체 루틴 조정"   // 제목 생성·변경 시에만 값, 이외 null — 목록 화면 갱신용
}
```

오류: 400(길이) / 401 / 403 / 404(→ 클라는 새 스레드 생성 유도) / 429 `RATE_LIMITED` / 503 `AI_UNAVAILABLE`

## 7. responseScheme · payload 계약

`responseScheme`이 payload의 존재·구조를 완전히 결정한다. `text`인데 payload가 있거나 `chart`인데 없으면 서버 버그.

| responseScheme | content | payload |
| --- | --- | --- |
| `text` | 답변 전문 | 항상 `null` (키는 유지) |
| `chart` | 차트 설명 문장 | 아래 chart payload |
| `exerciseGif` | 운동 설명 | `{ "exerciseId": "uuid", "exerciseName": "..." }` — GIF URL은 클라가 종목 상세 API로 획득 |
| `routine` | 추천 이유 | §1 `data.routine`과 동일 구조 — 추천 시점 스냅샷 (원본 수정·삭제와 무관하게 보존) |

chart payload:

```json
{
  "chartType": "line",              // line | bar — 색·스타일은 클라 소유, 서버는 데이터만
  "title": "최근 4주 가슴 볼륨",
  "xLabel": "주차", "yLabel": "볼륨(kg)",
  "x": ["6/29", "7/6", "7/13", "7/20"],
  "series": [ { "name": "가슴", "values": [4200, 4650, 4400, 5100] } ]
}
```

## 8. 에러 코드 (01 카탈로그 외 신규)

| code | HTTP | 상황 |
| --- | --- | --- |
| `THREAD_NOT_FOUND` | 404 | 스레드 없음, TTL 만료 |
| `THREAD_FORBIDDEN` | 403 | 타 유저 스레드 접근 |
| `NO_ROUTINE_CANDIDATE` | 409 | 조건 만족 루틴 구성 불가 |
| `AI_UNAVAILABLE` | 503 | LLM 호출 실패 — 클라는 재시도 UI |
| `RATE_LIMITED` | 429 | 요청 빈도 초과 |

## 9. 협의 포인트 (미확정)

1. Gateway 라우팅 prefix `/ai` — 버저닝(`/ai/v1`) 여부
2. 스트리밍 — MVP 동기 JSON, SSE 도입 시점·게이트웨이 SSE 통과 설정
3. 스레드 5개 초과 — 자동 삭제(+`deletedThreadId` 반환) vs 409 후 유저 직접 삭제
4. 레이트리밋 — 메시지 분당·루틴 생성 일일 상한, Gateway 적용 여부
5. `responseScheme` 표기 — 와이어 camelCase(`exerciseGif`), 내부 DB snake(`exercise_gif`)
6. `context` 자유 텍스트 — 프롬프트 인젝션 대비 (길이 제한 + 시스템 프롬프트 격리)
