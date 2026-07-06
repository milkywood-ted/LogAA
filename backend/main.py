from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import puller, analyze, settings, user_logs, files, profiles, cases, history

app = FastAPI(title="LogAA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
