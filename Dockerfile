# FitSet AI 서버 — ECS(Fargate/EC2) 배포용 이미지
# 빌드:  docker build -t fitset-ai-server .
# 실행:  docker run -p 8000:8000 fitset-ai-server
#        (AWS 자격은 ECS 태스크 역할이 주입 — dynamodb:Scan/GetItem, ssm:GetParameter, bedrock:InvokeModel)
FROM python:3.12-slim

WORKDIR /srv

# 의존성 레이어 분리 — 코드 변경 시 재설치 방지
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# 애플리케이션 + 종목 마스터 데이터 (exercise-metadata만 필요)
COPY app/ app/
COPY data/exercise-metadata.ko.json data/exercise-metadata.ko.json

EXPOSE 8000

# 부팅 시 routines 전량 로드(수십 초) — ALB 헬스체크는 /health 200 매칭,
# healthCheckGracePeriod를 충분히(예: 180s) 잡아 로드 중 태스크가 죽지 않게 한다.
# --limit-concurrency 35 = executor 스레드(EXECUTOR_MAX_WORKERS, 기본 24) + 대기열 여유.
# 초과 요청은 즉시 503 → 클라 30s 타임아웃 전에 실패를 알린다. 스레드 수 조정 시 함께 조정.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "35"]
