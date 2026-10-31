from fastapi import FastAPI

from app.auth import router as auth_router
from app.moods.router import router as moods_router
from app.config import get_settings

app = FastAPI(title="dampingi", description="FastAPI backend backed by Supabase Auth")

app.include_router(auth_router)
app.include_router(moods_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "env": get_settings().app_env}
