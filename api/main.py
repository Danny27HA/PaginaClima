from fastapi import FastAPI
from .routers import system

app = FastAPI(title="CDMX Flood API", version="0.1.0")
app.include_router(system.router)

@app.get("/")
def root():
    return {"msg": "CDMX Flood API lista", "docs": "/docs"}