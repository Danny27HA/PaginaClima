from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import system, forecast, score, chat

app = FastAPI(title="CDMX Flood API", version="0.1.0")

# CORS de desarrollo (abrimos todo para que el HTML local pueda pegarle)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(forecast.router)
app.include_router(score.router)
app.include_router(chat.router)

@app.get("/")
def root():
    return {"msg": "CDMX Flood API lista", "docs": "/docs"}

# ======= Carga automática al arranque =======
@app.on_event("startup")
def prime_openmeteo_on_startup():
    """
    Carga pronóstico 72h de Open-Meteo para CDMX al iniciar el servidor.
    Si ya hay datos de una corrida previa en esa ventana, se limpian.
    """
    try:
        from .routers.forecast import OpenMeteoReq, load_openmeteo
        req = OpenMeteoReq(
            bbox="-99.36,19.18,-98.94,19.59",  # CDMX aprox
            step_deg=0.06,                      # rejilla ligera (~6 km)
            hours=72,
            clear_previous=True
        )
        # Llamada síncrona; el server terminará de levantar tras cargar
        load_openmeteo(req)
        print("[startup] Open-Meteo cargado correctamente.")
    except Exception as e:
        # No reventamos el arranque si falla la carga
        print(f"[startup] Open-Meteo falló: {e!r}")
