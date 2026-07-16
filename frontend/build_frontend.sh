#!/bin/sh
# 운영 배포용 빌드 — 산출물(dist/)을 backend(:8800)가 직접 서빙한다.
# 프론트 코드 수정을 운영에 반영할 때 실행한다 (서버 아님, 빌드 후 종료).
#
# 개발 중 HMR이 필요하면 ./run_frontend.sh (dev 서버 :5173, .env.development 사용)
npm run build
echo ""
echo "빌드 완료 — backend(:8800)가 dist/ 를 서빙합니다. 접속: http://<서버IP>:8800"
