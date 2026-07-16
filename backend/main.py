from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from config import config
from middleware.ip_allowlist import IPAllowlistMiddleware
from routers import puller, analyze, settings, user_logs, files, profiles, cases, history

app = FastAPI(title="LogAA API")

# CORS는 개발 워크플로(npm run dev, :5173 → :8800 교차 출처) 전용이다.
# 운영에서는 backend가 frontend 빌드 산출물을 직접 서빙(하단 mount)하므로
# 같은 출처가 되어 CORS가 관여하지 않는다.
# 이 시스템은 인증 헤더·쿠키를 쓰지 않으므로(frontend 설계서 C2) credentials는
# 끈다. allow_credentials=True + allow_origins=["*"] 는 CORS 스펙상 무효 조합이라
# credentials를 끄면 "*" 가 그대로 유효해진다. 실제 접근 통제는 IP 허용목록이 담당.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# IP 허용목록 (config allowed_client_ips) — 미설정 시 전체 허용(opt-in).
# Starlette은 나중에 등록한 미들웨어가 바깥(먼저 실행)이므로, 허용되지 않은
# IP를 다른 처리보다 앞서 차단하도록 CORS 뒤에 등록한다.
app.add_middleware(
    IPAllowlistMiddleware,
    allowed_ips=config.allowed_client_ips(),
)

app.include_router(puller.router)
app.include_router(analyze.router)
app.include_router(settings.router)
app.include_router(user_logs.router)
app.include_router(files.router)
app.include_router(profiles.router)
app.include_router(cases.router)
app.include_router(history.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Frontend 정적 서빙 (SPA) ──────────────────────────────────────────────────
# frontend 빌드 산출물(dist/)을 backend가 직접 서빙한다 (frontend 설계서 §9-5).
# 별도 frontend 서버 프로세스가 필요 없고, 같은 출처라 CORS도 관여하지 않는다.
# 마운트는 모든 API 라우트 뒤에 두어 /api/*·/health·/docs가 우선 매칭된다.

class SPAStaticFiles(StaticFiles):
    """정적 파일 404 시 index.html로 폴백한다.

    react-router의 클라이언트 라우트(/settings, /cases 등)를 직접 접속하거나
    새로고침해도 SPA가 로드되도록 한다.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            # API 경로는 폴백 대상이 아니다 — 미정의 /api/* 는 404 그대로 반환
            if e.status_code == 404 and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
# dist가 없으면(프론트 미빌드) API 서버로만 동작한다 — frontend/build_frontend.sh로 빌드.
