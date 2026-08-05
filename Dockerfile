# FitSet AI 서버 — ECS(Fargate/EC2) 배포용 이미지
# 빌드:  docker build -t fitset-ai-server .
# 실행:  docker run -p 8000:8000 fitset-ai-server
#        (AWS 자격은 ECS 태스크 역할이 주입 — dynamodb:Scan/GetItem, ssm:GetParameter, bedrock:InvokeModel)
FROM python:3.12-slim

# uv 바이너리만 복사 — uv.lock의 고정 버전 그대로 설치해 로컬과 배포를 일치시킨다
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv

# 의존성 레이어 분리 — 코드 변경 시 재설치 방지. 프로젝트 자신은 제외(--no-emit-project)해
# app/ 복사 전에도 설치가 성립한다 (packages=["app"]인 휠 빌드가 여기서 돌면 실패)
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
 && uv pip install --system --no-cache -r /tmp/req.txt

# 애플리케이션 + 종목 마스터 데이터 (exercise-metadata만 필요)
COPY app/ app/
COPY data/exercise-metadata.ko.json data/exercise-metadata.ko.json
# 배치 스크립트 — 같은 이미지를 EventBridge Scheduler가 ECS RunTask로 띄워 CMD만 바꿔 실행한다
# (종목 카탈로그 일 1회 동기화). 서버 실행에는 쓰이지 않는다
COPY scripts/ scripts/

EXPOSE 8000

# 부팅 시 routines 전량 로드(수십 초) — ALB 헬스체크는 /health 200 매칭,
# healthCheckGracePeriod를 충분히(예: 180s) 잡아 로드 중 태스크가 죽지 않게 한다.
# --limit-concurrency 35 = executor 스레드(EXECUTOR_MAX_WORKERS, 기본 24) + 대기열 여유.
# 초과 요청은 즉시 503 → 클라 30s 타임아웃 전에 실패를 알린다. 스레드 수 조정 시 함께 조정.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "35"]
