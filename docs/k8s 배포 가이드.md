# k8s 배포 가이드

AI 서버가 EKS 클러스터 `fitset`에 어떻게 올라가는지, 이 레포와 fitset-infra 레포가 각각 무엇을 책임지는지 정리한다. 인프라 정본은 https://github.com/asm-hangang/fitset-infra 이고 여기서는 AI 서버 관점만 다룬다.

## 1. 책임 경계

| 레포 | 책임 | 산출물 |
|---|---|---|
| fitset-ai-server | 이미지 빌드와 ECR push | `729743892772.dkr.ecr.ap-northeast-2.amazonaws.com/fitset-ai-server:{SHA}` |
| fitset-infra | 배포 대상과 설정 | `gitops/charts/ai-chat-api/values-{env}.yaml`의 `image.tag`, env, 리소스, 프로브 |
| ArgoCD | git 감지 후 클러스터 반영 | 네임스페이스 `fitset-stage`, `fitset-prod` |

차트 이름은 `ai-chat-api`, ECR 레포 이름은 `fitset-ai-server`다. ALB는 `/ai/*`만 이 서버로 보낸다.

## 2. 배포 흐름

```
main 머지
  → .github/workflows/cd.yml 이 ECR 에 :{SHA} 와 :latest push (약 1분)
  → fitset-infra values-stage.yaml 의 image.tag 를 그 SHA 로 갱신, push
  → ArgoCD 가 감지해 fitset-stage 롤링 업데이트
  → 검증 후 values-prod.yaml 에 같은 SHA 를 적어 prod 반영
```

stage에서 검증한 바로 그 이미지가 prod로 간다. 롤백은 `image.tag`를 이전 SHA로 되돌리는 커밋 하나다. 2026-08-28 현재 `image.tag` 갱신은 수동이고, cd.yml에 fitset-infra 커밋 단계를 넣는 것은 로드맵 항목이다.

## 3. 파드에 들어가는 설정

| 종류 | 출처 | 예 |
|---|---|---|
| 평문 env | `values-{env}.yaml`의 `env` | `JWKS_URL`, `MYSQL_HOST` |
| 비밀 | SSM `/fitset/{env}/...`를 ExternalSecret이 Secret으로 동기화, `envFrom` 주입 | `MYSQL_PASSWORD` |
| AWS 자격 | Pod Identity, 역할 `fitset-ai-chat-api` (DynamoDB 접두사 4종, Bedrock) | 코드에 키 없음 |

빈 문자열 env는 차트가 키 자체를 내보내지 않는다. `MYSQL_HOST`를 비우면 NL2SQL 툴만 꺼지고 나머지는 정상 동작한다. SSM 값을 바꿔도 파드는 자동 재시작되지 않으므로 `kubectl -n fitset-stage rollout restart deploy/ai-chat-api`로 반영한다.

## 4. 리소스와 프로브

| 항목 | prod | stage |
|---|---|---|
| requests | 500m, 1Gi | 200m, 512Mi |
| limits memory | 2Gi | 1Gi |
| replicaCount | 2 | 1 |
| startupProbe | `/health` 5초 간격 24회, 최대 120초 | 같음 |
| terminationGracePeriodSeconds | 120 | 같음 |

`/health`는 프로세스 생존만 본다. 루틴 스토어 부팅 로드를 뺐으므로(2026-08-27) 부팅은 몇 초이고 메모리는 수백 MB다. 루틴 검색이 Postgres로 옮겨가면 `/health`에 `SELECT 1`을 더한다.

## 5. 배치

종목 카탈로그 동기화(`scripts/sync_exercise_catalog.py`, 03:00 KST)는 같은 이미지에 command만 바꾼 CronJob으로 옮긴다. ECS 시절의 EventBridge Scheduler는 ECS 정리와 함께 지운다. stage에서 돌릴 때는 `BACKEND_EXERCISES_URL`을 stage 주소로 주고, DynamoDB 테이블을 prod와 분리한 뒤에 켠다.

## 6. 확인 명령

```bash
aws eks update-kubeconfig --name fitset --region ap-northeast-2
kubectl -n fitset-stage get pods -l app.kubernetes.io/name=ai-chat-api
kubectl -n fitset-stage logs deploy/ai-chat-api --tail=100
kubectl -n fitset-stage top pod
kubectl -n fitset-stage exec deploy/ai-chat-api -- curl -s localhost:8000/health
curl -s https://api-stage.fitset.kro.kr/ai/v1/health
```

ArgoCD UI는 `kubectl -n argocd port-forward svc/argocd-server 8080:80` 후 http://localhost:8080 이다.

## 7. 자주 나는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
| ImagePullBackOff | `image.tag`가 ECR에 없음, 오타 | `aws ecr describe-images --repository-name fitset-ai-server`로 태그 확인 |
| CrashLoopBackOff, exec format error | arm64 이미지 | cd.yml은 amd64로 빌드한다, 로컬 push라면 `--platform linux/amd64` |
| Ready 안 됨, startupProbe 실패 | 부팅 120초 초과 | 로그 확인, 필요 시 `probes.startup.failureThreshold` 상향 |
| OOMKilled | limit 초과 | `kubectl top pod`로 실측 후 limit 조정, 루틴 스토어 로드가 다시 들어갔는지 확인 |
| 전부 401 | `JWKS_URL`이 prod를 가리킴 | stage values의 `JWKS_URL` 확인 |
| envFrom 값이 안 바뀜 | Secret은 갱신됐지만 파드 미재시작 | `rollout restart` |
| ALB 503 | 타깃 그룹에 Ready 파드 없음 | 위 항목들 순서대로 |

## 8. 로컬에서 이미지 확인

```bash
docker build --platform linux/amd64 -t fitset-ai-server .
docker run --rm -p 8000:8000 -v ~/.aws:/root/.aws:ro -e AWS_PROFILE=default fitset-ai-server
curl -s localhost:8000/health
```

ECR에 직접 push하지 않는다. main 머지가 유일한 push 경로다.
