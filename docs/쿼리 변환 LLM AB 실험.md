# 쿼리 변환 LLM vs 템플릿 폴백 — A/B 실험 (2026-07-29)

루틴 추천 파이프라인 ⑥단계의 **쿼리 변환 LLM**(`_describe_query`)이 응답 지연의 절반을 차지한다.
이 호출을 템플릿 조립(현재 폴백 경로)으로 대체해도 되는지 검증했다.

## 요약

- 요청당 실측 지연은 **약 3.6초**, 그중 **Bedrock LLM 2회가 85%**(3.1초). 룰 필터·코사인·랭킹은 다 합쳐 0.7%.
- 변환 LLM을 템플릿으로 바꾸면 **추천 결과가 사실상 완전히 달라진다** — 탑30 겹침 22%, 탑1 일치 **0/10**.
- 그 차이는 `context`(자유 텍스트) 때문이 **아니다**. context가 없는 케이스도 동일하게 갈렸다.
- LLM-as-judge 실험은 **위치 편향으로 무효**(심판이 B 위치를 8/10회 선택).
- 대신 선택된 루틴의 실제 구성을 **수기로 판정**한 결과 **LLM 4승 / 무승부 1 / 템플릿 0승** (§6-1). 표본 5건, 비블라인드.
- **결론: 현행 유지.** 템플릿 승리 사례가 0건이며, 교체 시 품질 하락 가능성이 높다.

---

## 1. 실험 환경

| 항목 | 값 |
| --- | --- |
| LLM | `global.anthropic.claude-haiku-4-5-20251001-v1:0` (Bedrock, ap-northeast-2) |
| 임베딩 | `global.cohere.embed-v4:0` (1024d float32, `input_type=search_query`) |
| 루틴 데이터 | DynamoDB `routines` 전량 — **67,787건** |
| 측정 위치 | 로컬 macOS → 서울 리전 (클러스터 안 아님) |
| 실행일 | 2026-07-29 |
| 총 API 호출 | LLM 20회(변환 10 + 판정 10), 임베딩 20회 — 약 $0.03 |

부팅 시 스토어 로딩(DynamoDB Scan → 인메모리)은 **142~154초** 걸렸다.
ALB `healthCheckGracePeriod` 180초 설정이 아슬아슬하다.

---

## 2. 워크로드 지연 실측

각 5회 호출의 중앙값.

| 단계 | 지연 | 비중 |
| --- | ---: | ---: |
| ① 쿼리 변환 LLM (`max_tokens=200`) | **1,755 ms** | 48% |
| ② 쿼리 임베딩 (Cohere v4) | **477 ms** | 13% |
| ③ 최종 선택 LLM (`max_tokens=10`) | **1,346 ms** | 37% |
| ④ DynamoDB GetItem | 19 ms | 0.5% |
| 룰 필터 + 코사인 + 랭킹 (인메모리) | ~24 ms | 0.7% |
| **합계** | **≈ 3,620 ms** | |

- 편차: ① 1,627~2,327ms, ② 266~612ms, ③ 780~1,434ms
- 스프링 내부 API 2건은 ①과 `asyncio.gather`로 병렬 실행되어 ①의 1.7초 안에 묻힌다(로컬 미측정).
- ③은 출력이 10토큰뿐인데도 1.3초 — 후보 5개의 종목 목록이 입력으로 들어가 프리필 비용이 붙는다.
- 클라이언트 타임아웃 30초 대비 여유는 충분하다.

**인메모리 검색 최적화는 의미가 없다.** 필터·코사인·랭킹을 전부 합쳐도 24ms로 전체의 0.7%다.
지연은 전적으로 Bedrock 호출 2회가 결정한다.

---

## 3. 방법

10개 시나리오(context 있음 5 / 없음 5)에 대해 두 경로를 나란히 실행했다.

```
요청(goal·level·muscles·minutes·context)
   │
   ├─[A] 변환 LLM ──→ 묘사문 ──→ 임베딩 ──┐
   │                                      ├──→ 동일 후보군에서 코사인 탑30 → 비교
   └─[B] 템플릿 조립 ─→ 묘사문 ──→ 임베딩 ─┘
```

- 룰 필터는 양쪽에 **동일하게** 적용(`avoided=∅`, `home_only=False`)해 후보군을 고정했다.
- 실제 파이프라인의 `랜덤 5개 샘플 → 선택 LLM` 단계는 **제외**했다. 랜덤이 노이즈를 넣어 묘사문의 효과를 가리기 때문에, 코사인 탑1로 직접 비교했다.
- 측정 지표: 탑30 겹침 비율, 탑1 일치 여부.

시나리오는 `hypertrophy/strength/weightLoss/endurance` 4개 목표, `beginner/intermediate/advanced` 3개 수준,
후보 풀 162건~20,654건에 걸치도록 구성했다.

---

## 4. 실험 1 — 탑30 겹침

| # | context | 후보 수 | 탑30 겹침 | 탑1 일치 |
| ---: | :---: | ---: | ---: | :---: |
| 1 | 있음 | 4,122 | 23% | ✗ |
| 2 | 있음 | 20,654 | 13% | ✗ |
| 3 | 있음 | 374 | 10% | ✗ |
| 4 | 있음 | 162 | 77% | ✗ |
| 5 | 있음 | 1,076 | 3% | ✗ |
| 6 | 없음 | 4,122 | 0% | ✗ |
| 7 | 없음 | 20,654 | 13% | ✗ |
| 8 | 없음 | 374 | 0% | ✗ |
| 9 | 없음 | 162 | 80% | ✗ |
| 10 | 없음 | 1,076 | 0% | ✗ |

| 구분 | 탑30 평균 겹침 | 탑1 일치율 |
| --- | ---: | ---: |
| context 있음 (n=5) | 25% | **0%** |
| context 없음 (n=5) | 19% | **0%** |
| 전체 (n=10) | **22%** | **0%** |

### 관찰 1 — 교체는 무해하지 않다

10건 전부 탑1이 다르고 탑30도 5분의 1만 겹친다.
"차이가 작으면 1.7초 절약" 가설은 기각. **교체 시 추천 결과가 바뀐다.**

### 관찰 2 — 차이의 원인은 context가 아니다

템플릿은 `context`를 아예 사용하지 않는다. 따라서 **context 없는 5건은 두 경로의 입력 정보량이 동일**하다.
그런데도 겹침은 19%로, context 있는 쪽(25%)과 다르지 않다.

즉 변환 LLM이 하는 일은 "자유 텍스트 해석"이 아니라 **문장을 임베딩 공간의 다른 위치로 옮기는 것**이다.
임베딩 문서가 "한글 종목명 나열 + description"으로 구성되어 있어,
종목명이 들어간 LLM 묘사문과 종목명이 하나도 없는 템플릿 묘사문이 서로 먼 곳에 찍힌다.

### 관찰 3 — 후보 풀이 작으면 겹친다

162건 → 77~80%, 20,654건 → 13%, 1,076~4,122건 → 0~23%.
고를 것이 적으면 묘사문이 무엇이든 같은 답이 나온다.

---

## 5. 실험 2 — LLM-as-judge (⚠️ 무효)

두 경로의 코사인 탑1 루틴을 심판 LLM(동일 Haiku 4.5)에 A/B로 제시하고
요청에 더 부합하는 쪽을 고르게 했다. 위치 편향 상쇄를 위해 홀수 시나리오는 LLM을 A,
짝수는 템플릿을 A로 배치했다.

**표면 결과: LLM 5승 / 템플릿 5승 (동점)**

그러나 위치별로 분해하면 결과가 뒤집힌다.

| LLM 위치 | 심판 선택 | 승자 |
| :---: | :---: | --- |
| A | **B** | 템플릿 |
| B | **B** | LLM |
| A | **B** | 템플릿 |
| B | **B** | LLM |
| A | **B** | 템플릿 |
| B | **B** | LLM |
| A | A | LLM |
| B | A | 템플릿 |
| A | **B** | 템플릿 |
| B | **B** | LLM |

**심판은 내용과 무관하게 B 위치를 8/10회 선택했다.**
5:5 동점은 좌우 교대 배치가 이 편향을 기계적으로 절반씩 나눈 결과이지,
두 경로의 품질이 같다는 증거가 아니다.

3번 시나리오에서 심판이 *"Both routines are identical"*이라고 답한 것도 신뢰도를 낮춘다
— 실제로는 서로 다른 루틴(`...-w2-d3` vs `...-w1-d3`)이었다.

**이 실험으로는 어느 쪽이 나은지 판단할 수 없다.**

---

## 6. 예시 5건 — 입력과 각 경로의 선택

### 예시 1. 가슴·삼두 / 중급 / 근비대 / 45분

**요청**
```
goal: hypertrophy | level: intermediate | muscles: chest, triceps | duration: 45min
context: "어깨가 조금 아파서 무리하지 않는 걸로. 벤치프레스는 좋아합니다."
```
| 경로 | 묘사문 | 선택된 루틴 |
| --- | --- | --- |
| **LLM** | *Perform 4 sets of barbell bench press (8-10 reps) as your main lift, followed by 3 sets of incline dumbbell press and 3 sets of cable flyes for chest. Finish with 3 sets of rope pu…* | `5-day-ulppl-w2-d3` |
| **템플릿** | *A intermediate hypertrophy workout routine targeting chest, triceps, about 45 minutes.* | `rat-race-w7-d1` |

후보 4,122건 · 탑30 겹침 23%

**선택된 루틴의 실제 구성**

| | LLM `5-day-ulppl-w2-d3` | 템플릿 `rat-race-w7-d1` |
| --- | --- | --- |
| 태그 | beginner / **hypertrophy** / 50min | beginner / **strength** / 50min |
| 운동 | 바벨 인클라인 벤치 3×8, 덤벨 스컬크러셔 3×8, 덤벨 시티드 오버헤드 프레스 3×8, 케이블 바 푸시다운 2×8, 덤벨 레터럴 레이즈 3×5, 덤벨 벤치 1×8 | 덤벨 벤치 3×2, 풀업 1×5, 덤벨 인클라인 벤치 3×9, 바벨 벤트오버 로우 3×10, 덤벨 레터럴 레이즈 3×11, 머신 V바 푸시다운 3×11 |

**판정: LLM 우세**

- goal 태그가 요청(근비대)과 일치하는 쪽은 LLM뿐이다. 템플릿은 strength.
- 템플릿의 덤벨 벤치 **3×2회**는 근력 영역 렙수로, 근비대 요청에 맞지 않는다.
- 템플릿은 45분 세션에 등 운동(풀업·벤트오버 로우)을 2개 넣어 요청 부위 밖으로 시간을 쓴다.
- **단, "어깨가 아프다"는 양쪽 모두 반영 실패.** LLM 루틴에는 어깨 통증에 가장 부적합한 **덤벨 시티드 오버헤드 프레스 3세트**가 포함되어 있다.

---

### 예시 2. 등·이두 / 고급 / 근력 / 60분

**요청**
```
goal: strength | level: advanced | muscles: back, biceps | duration: 60min
context: "데드리프트 위주로 무겁게 가고 싶어요."
```
| 경로 | 묘사문 | 선택된 루틴 |
| --- | --- | --- |
| **LLM** | *Perform heavy deadlifts (5x3-5 reps) as your main lift, followed by weighted pull-ups (4x5-8 reps) and barbell rows (4x6-8 reps) for back strength. Finish with barbell curls (3x8-1…* | `3-day-week-workout-plan-tailored-to-my-goals-w2-d1` |
| **템플릿** | *A advanced strength workout routine targeting back, biceps, about 60 minutes.* | `olympus-ascension-w12-d5` |

후보 20,654건 · 탑30 겹침 13%

**선택된 루틴의 실제 구성**

| | LLM `3-day-week-...-w2-d1` | 템플릿 `olympus-ascension-w12-d5` |
| --- | --- | --- |
| 태그 | beginner / strength / 70min / back·biceps | beginner / hypertrophy / 60min / **back·core·hamstrings** |
| 운동 | 풀업 3×14, 친업 3×12, 바벨 벤트오버 로우 2×5, 바벨 컬 3×10, 케이블 바 컬 2×10 | 덤벨 레그 컬 4×12, **덤벨 레그 컬 4×12(중복)**, 머신 뉴트럴 로우 3×12, 머신 45도 백 익스텐션 3×12 |

**판정: LLM 압승**

- 템플릿 루틴에는 **이두 운동이 하나도 없다.** 요청 부위의 절반을 통째로 빠뜨리고 햄스트링 레그 컬을 준다.
- 같은 운동(덤벨 레그 컬)이 **두 번 중복**으로 들어간 데이터 품질 문제도 있다.
- LLM 쪽은 등·이두 부위가 정확하고 goal 태그도 strength로 일치한다.
- **단, "데드리프트 위주로 무겁게"는 반영 실패.** 데드리프트가 없고, 렙수 12~14회는 근력(1~5회)이 아닌 근비대·근지구력 영역이다.

---

### 예시 3. 대퇴사두·둔근 / 초급 / 체중감량 / 30분

**요청**
```
goal: weightLoss | level: beginner | muscles: quadriceps, glutes | duration: 30min
context: "집에 기구가 없어서 맨몸으로만 하고 싶어요."
```
| 경로 | 묘사문 | 선택된 루틴 |
| --- | --- | --- |
| **LLM** | *Perform 30 minutes of bodyweight exercises targeting glutes and quadriceps for weight loss. Include squats, lunges, step-ups, and glute bridges with minimal rest between sets…* | `body-weight-strength-and-condition-training-w2-d3` |
| **템플릿** | *A beginner weightLoss workout routine targeting quadriceps, glutes, about 30 minutes.* | `body-weight-strength-and-condition-training-w1-d3` |

후보 374건 · 탑30 겹침 10%
같은 프로그램의 다른 주차(w2-d3 vs w1-d3)를 골랐다.
**LLM은 "맨몸으로만"이라는 context를 묘사문에 반영했지만, 룰 필터가 이미 장비를 거르므로 이 부분은 중복이다.**

**선택된 루틴의 실제 구성**

| | LLM `...-w2-d3` | 템플릿 `...-w1-d3` |
| --- | --- | --- |
| 태그 | beginner / hypertrophy / 30min / bodyweight | beginner / hypertrophy / 30min / bodyweight |
| 운동 | 워킹 런지 5×9, 점프 스쿼트 3×**18** | 워킹 런지 5×10, 점프 스쿼트 3×**10** |

**판정: 무승부 (LLM 근소 우위)**

- 같은 프로그램의 다른 주차라 구성이 사실상 동일하고, 렙수만 다르다.
- 체중감량 목표에서는 고반복이 유리하므로 점프 스쿼트 18회인 LLM 쪽이 근소하게 낫지만, 유의미한 차이로 보기 어렵다.
- 양쪽 모두 goal 태그가 `hypertrophy`인데 요청은 `weightLoss`다 — 룰 필터에 goal 조건이 없기 때문이며, 경로 선택과 무관한 별개 이슈다.

---

### 예시 4. 코어 / 중급 / 지구력 / 20분

**요청**
```
goal: endurance | level: intermediate | muscles: core | duration: 20min
context: "허리 디스크가 있어서 굽히는 동작은 피하고 싶습니다."
```
| 경로 | 묘사문 | 선택된 루틴 |
| --- | --- | --- |
| **LLM** | *Perform 20 minutes of core endurance work **avoiding spinal flexion**: planks (front and side), dead bugs, bird dogs, pallof presses, and standing core rotations. Maintain neutral spin…* | `pr-fitness-w3-d3` |
| **템플릿** | *A intermediate endurance workout routine targeting core, about 20 minutes.* | `titanium-w3-d2` |

후보 162건 · 탑30 겹침 77%

**LLM만이 "굽히는 동작 회피"를 "avoiding spinal flexion"으로 번역해 검색 쿼리에 넣었다.**
룰 필터에는 이런 조건을 표현할 필드가 없으므로, 이 정보는 오직 임베딩 경로로만 전달된다.
템플릿 경로에서는 이 요구가 완전히 소실된다.

**선택된 루틴의 실제 구성**

| | LLM `pr-fitness-w3-d3` | 템플릿 `titanium-w3-d2` |
| --- | --- | --- |
| 태그 | beginner / strength / 20min / bodyweight·dumbbell | beginner / strength / 20min / machine |
| 운동 | **덤벨 싯업 1×4**, 핸드 플랭크 3×12, 엘보 사이드 플랭크 6×12, **덤벨 싯업 5×12** | 머신 45도 백 익스텐션 2×9, **머신 크런치 2×10** |
| 척추 굴곡 세트 | 6세트 | 2세트 |
| 중립 척추 세트 | 9세트 | 0세트 |

**판정: LLM 근소 우위 — 다만 양쪽 모두 요구 반영 실패**

- LLM은 플랭크 계열 9세트로 중립 척추 유지 동작을 제공해 디스크·지구력 양쪽에 부합한다. 템플릿은 총 4세트뿐이라 20분 세션을 채우지도 못한다.
- **그러나 LLM이 고른 루틴에는 덤벨 싯업이 6세트 들어 있다.** 척추 굴곡은 디스크 환자에게 금기이며, 절대량으로는 템플릿(크런치 2세트)보다 많다.
- 즉 **묘사문에는 `avoiding spinal flexion`이 정확히 반영됐지만 검색 결과가 따라오지 못했다.** 묘사문 품질과 최종 추천 품질은 별개라는 것을 보여주는 사례다. 위 문단의 "임베딩 경로로 전달된다"는 서술은 쿼리 단계에 한정해 읽어야 한다.

---

### 예시 5. 어깨 / 초급 / 근비대 / 40분

**요청**
```
goal: hypertrophy | level: beginner | muscles: shoulders | duration: 40min
context: "어깨가 좁아서 넓어 보이고 싶어요."
```
| 경로 | 묘사문 | 선택된 루틴 |
| --- | --- | --- |
| **LLM** | *Perform 4 sets of shoulder presses (3x8-10 reps), followed by 3 sets of lateral raises (3x10-12 reps) and 3 sets of reverse flyes (3x10-12 reps). Rest 90 seconds between sets…* | `noexcusesnoexcuser-w7-d3` |
| **템플릿** | *A beginner hypertrophy workout routine targeting shoulders, about 40 minutes.* | `40-min-1-5x-week-upper-focused-p-p-l-hypertrophy-w8-d4` |

후보 1,076건 · 탑30 겹침 3%

**선택된 루틴의 실제 구성**

| | LLM `noexcusesnoexcuser-w7-d3` | 템플릿 `40-min-...-hypertrophy-w8-d4` |
| --- | --- | --- |
| 태그 | beginner / **strength** / 40min | beginner / **hypertrophy** / 40min |
| 운동 | 머신 풀다운 1×8, 덤벨 인클라인 벤치 1×8, **덤벨 시티드 리어 델트 플라이 2×12**, 머신 펙 플라이 2×18, 머신 딥스 1×9, **덤벨 레터럴 레이즈 2×11** | 풀업 1×9, 바벨 업라이트 로우 3×9, 덤벨 시티드 오버헤드 트라이셉 익스텐션 3×9 |

**판정: LLM 우세**

- "어깨가 넓어 보이고 싶다"는 **측면 삼각근** 발달 요구다. 정확한 처방인 **레터럴 레이즈**는 LLM 루틴에만 있고, 리어 델트 플라이까지 더해 삼각근 후면도 커버한다.
- 템플릿의 업라이트 로우는 승모근 기여가 커서 "넓어 보이기"와 방향이 다르고, 트라이셉 익스텐션은 요청 부위(어깨)가 아니다.
- 템플릿은 총 7세트로 40분 세션을 채우기에 부족하다.
- goal 태그만 보면 템플릿(hypertrophy)이 맞지만, **실제 운동 구성의 요청 부합도는 LLM이 명확히 높다.**

---

## 6-1. 판정 종합 (수기 평가)

§5의 LLM-as-judge가 위치 편향으로 무효였으므로, 위 5건에 대해 **선택된 루틴의 실제 운동 구성을 직접 확인해 수기로 판정**했다.

| 예시 | 판정 | 결정적 근거 |
| ---: | --- | --- |
| 1 | **LLM** | 템플릿은 goal=strength에 벤치 3×2회 — 근비대 요청에 근력 렙수 |
| 2 | **LLM 압승** | 템플릿에 이두 운동이 0개, 햄스트링 레그컬이 중복으로 등장 |
| 3 | 무승부 | 같은 프로그램의 다른 주차, 렙수만 상이 |
| 4 | LLM 근소 | 양쪽 모두 디스크 요구 반영 실패, LLM은 플랭크 9세트로 상대 우위 |
| 5 | **LLM** | "넓어 보이기"의 정답인 레터럴 레이즈가 LLM에만 존재 |

**결과: LLM 4승 / 무승부 1 / 템플릿 0승**

**단서 두 가지**

1. **블라인드 평가가 아니다.** 평가자가 어느 쪽이 LLM 경로인지 알고 판정했으므로 확증 편향 가능성이 있다. 다만 예시 2(이두 0개)와 예시 5(레터럴 레이즈 유무)는 편향과 무관한 객관적 사실이다.
2. **표본 5건이다.** 방향성 확인용이며 통계적 결론이 아니다.

**해석**: 템플릿 경로가 이긴 사례가 0건이라는 점은, 변환 LLM 제거 시 추천 품질이 **하락할 가능성이 높다**는 것을 시사한다. 다만 LLM 경로도 완벽하지 않다 — 예시 1(어깨 통증)과 예시 4(척추 굴곡)에서 context가 최종 결과에 반영되지 못했다.

---

## 7. 해석

**변환 LLM이 실제로 기여하는 것은 두 가지다.**

1. **어휘 정합** — 임베딩 문서가 종목명 나열이므로, 종목명을 생성하는 LLM 묘사문이 문서와 같은 어휘 공간에 놓인다. 템플릿은 종목명이 없어 구조적으로 불리하다. (context 없는 케이스도 갈린 이유)
2. **룰 필터로 표현 불가능한 제약 전달** — 예시 4의 "굽히는 동작 회피"처럼 enum 필드에 없는 요구는 임베딩 경로로만 갈 수 있다. 템플릿에서는 소실된다.

**반면 중복인 부분도 있다.** 예시 3의 "맨몸으로만"은 이미 `home_only` 룰 필터가 처리하므로 묘사문에 다시 넣을 필요가 없다.

**따라서 "1.7초를 없앤다"는 목표는 전부 아니면 전무가 아니다.** context가 비어 있고 룰 필터로 충분히 표현되는 요청이라면 템플릿으로 충분할 수 있다 — 다만 위 1번(어휘 정합) 때문에 그마저도 검증이 필요하다.

---

## 8. 결론

**현행 유지.** 변환 LLM을 템플릿으로 대체하지 않는다.

근거:
- 교체 시 추천 결과가 완전히 바뀌며(탑1 일치 0/10), 품질 방향은 미검증이다.
- 예시 4처럼 룰 필터가 표현할 수 없는 사용자 요구가 템플릿 경로에서 소실된다.

### 다음 단계 (우선순위 순)

1. **판정 실험 재설계** — 위치 편향을 제거해야 한다. A/B 동시 제시 대신 **각 루틴을 독립적으로 1~5점 채점**하거나, 같은 쌍을 좌우 바꿔 2회 물어 일치할 때만 유효 표본으로 인정. 표본도 10건은 부족하다(30건 이상 권장).
2. **부팅 로딩 142초 개선** — DynamoDB Scan을 S3 스냅샷(`vectors.npy` + `routines.jsonl`) 다운로드로 대체. 배포·스케일아웃마다 발생하는 비용이며 헬스체크 유예시간과 직결된다.
3. **③ 선택 LLM 검토** — 출력 10토큰에 1.3초. 후보 5개의 종목 목록 입력을 줄이면(예: 종목명 8개 → 5개) 단축 여지가 있다.
4. **조건부 템플릿** — `context`가 비어 있을 때만 템플릿을 쓰는 절충안. 단 1번 검증이 선행되어야 한다.

---

## 9. 한계

- **표본 10건**은 통계적 결론을 내기에 부족하다. 경향 확인용이다.
- 판정 실험은 **위치 편향으로 무효**이며, 이 문서의 어떤 품질 주장도 이 실험에 근거하지 않는다.
- 실제 파이프라인의 `랜덤 5개 샘플 → 선택 LLM` 단계를 제외했으므로, 최종 사용자가 받는 결과의 차이는 여기서 측정한 것과 다를 수 있다(랜덤이 차이를 일부 희석할 것으로 예상).
- 지연 측정은 **로컬 → 서울 리전**이다. 클러스터 안에서는 Bedrock 구간이 비슷하거나 소폭 개선될 것으로 보이나 확인되지 않았다.
- 룰 필터 조건 중 `avoided`(기피 부위)와 `home_only`는 고정값을 사용했다. 실제로는 유저 프로필에 따라 후보군이 달라진다.

## 10. 재현

실험 스크립트는 세션 스크래치패드에 있다(리포지토리 미포함).

- `ab_describe.py` — 실험 1 (탑30 겹침)
- `ab_judge.py` — 실험 1 + 2 통합, 결과를 `ab_results.json`으로 저장
- `bench_latency.py` — 워크로드 구간별 지연 측정

```bash
AWS_REGION=ap-northeast-2 uv run python ab_judge.py
```

실행에는 Bedrock Anthropic 모델 접근 권한이 필요하다
(사용 사례 양식 제출 완료: 2026-07-29, `aws bedrock get-use-case-for-model-access`로 확인 가능).
