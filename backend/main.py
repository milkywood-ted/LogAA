from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import config
from middleware.ip_allowlist import IPAllowlistMiddleware
from routers import puller, analyze, settings, user_logs, files, profiles, cases, history

app = FastAPI(title="LogAA API")

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
