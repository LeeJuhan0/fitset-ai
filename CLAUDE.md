# FitSet AI 서버

운동 앱 FitSet의 AI 백엔드 — AI 채팅(스레드)과 루틴 생성·추천. FastAPI + LangGraph.
API Gateway가 인증 후 `X-User-Id` 헤더로 userId를 전달한다 — 이 서버에 인증 로직 없음, 내부망 전용.
저장소는 MongoDB(운영은 MongoDB 호환 DocumentDB 가능), 루틴 원본은 S3.

코드를 읽거나 수정하기 전에 반드시 참고할 것:

- [`docs/코드 아키텍처.md`](docs/코드%20아키텍처.md) — 디렉토리 구조, 계층 규칙, 데이터 규칙
- [`docs/코드 컨벤션.md`](docs/코드%20컨벤션.md) — 임포트·타입·docstring 등 파이썬 코드 규약
- [`docs/document-structure.md`](docs/document-structure.md) — MongoDB 도큐먼트 구조(ERD)와 payload JSON 규약
