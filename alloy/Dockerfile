# ECS에는 ConfigMap 같은 설정 주입 수단이 없어 설정 파일을 이미지에 함께 굽는다.
# 버전은 백엔드 사이드카와 통일(v1.18.1).
FROM grafana/alloy:v1.18.1

COPY config.alloy /etc/alloy/config.alloy
