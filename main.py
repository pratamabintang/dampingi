from typing import Optional

from fastapi import FastAPI

from config import settings

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World", "env": settings.app_env}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "env": settings.app_env}

@app.get("/items/{item_id}")
def read_item(item_id: int, q: Optional[str] = None):
    return {"item_id": item_id, "q": q}