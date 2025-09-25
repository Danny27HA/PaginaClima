import json
import math
import requests
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Any, Optional
from sqlalchemy import text
from ..db import engine
from dateutil import tz

router = APIRouter(prefix="/forecast", tags=["forecast"])

# ======= Modelos de entrada =======
class ForecastCell(BaseModel):
    ts: datetime = Field(..., description="Timestamp UTC de la celda")
    mm: float = Field(..., ge=0, description="Milímetros de lluvia (para el periodo dado)")
    geom: Any = Field(..., description="Polígono GeoJSON (WGS84) de la celda/área")

class ForecastPayload(BaseModel):
    horizon_h: int = Field(72, description="Ventana de pronóstico en horas (default 72)")
    cells: List[ForecastCell]

# ======= POST /forecast (carga manual) =======
@router.post("")
def load_forecast(payload: ForecastPayload):
    """
    Inserta/adjunta celdas de pronóstico en precip_forecast.
    Espera polígonos GeoJSON (WGS84), ts (UTC) y mm por celda.
    """
    if not payload.cells:
        raise HTTPException(status_code=400, detail="No hay celdas en el payload.")

    inserted = 0
    sql = text("""
        INSERT INTO precip_forecast (ts, mm, geom)
        VALUES (:ts, :mm, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)

    try:
        with engine.begin() as conn:
            for c in payload.cells:
                geom_json = json.dumps(c.geom)  # dict -> string JSON
                conn.execute(sql, {"ts": c.ts, "mm": c.mm, "geom": geom_json})
                inserted += 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Inserción falló: {e}")

    return {"ok": True, "inserted": inserted, "horizon_h": payload.horizon_h}

# ======= GET /forecast/summary =======
@router.get("/summary")
def forecast_summary(
    bbox: Optional[str] = None,
    from_hours: int = 0,
    to_hours: int = 72,
):
    """
    Devuelve suma de mm y conteo de celdas entre now()+from_hours y now()+to_hours.
    Opcionalmente filtra por bbox = 'minx,miny,maxx,maxy' (WGS84).
    """
    now_utc = datetime.now(tz=tz.UTC)
    t0 = now_utc + timedelta(hours=from_hours)
    t1 = now_utc + timedelta(hours=to_hours)

    base = """
        SELECT COUNT(*)::int AS n_cells, COALESCE(SUM(mm),0)::float AS mm_sum
        FROM precip_forecast
        WHERE ts >= :t0 AND ts <= :t1
    """

    params = {"t0": t0, "t1": t1}
    if bbox:
        try:
            minx, miny, maxx, maxy = [float(x) for x in bbox.split(",")]
        except Exception:
            raise HTTPException(status_code=400, detail="bbox debe ser 'minx,miny,maxx,maxy'")
        base += " AND ST_Intersects(geom, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326))"
        params.update({"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})

    with engine.connect() as conn:
        row = conn.execute(text(base), params).mappings().first()

    return {
        "window_utc": {"from": t0.isoformat(), "to": t1.isoformat()},
        "bbox": bbox,
        "n_cells": row["n_cells"],
        "mm_sum": row["mm_sum"],
    }

# ========= MODELO y endpoint Open-Meteo =========
class OpenMeteoReq(BaseModel):
    bbox: str = Field(..., description="minx,miny,maxx,maxy en WGS84 (ej. -99.36,19.18,-98.94,19.59)")
    step_deg: float = Field(0.06, ge=0.01, le=0.2, description="Tamaño de celda (grados). 0.06 ≈ ~6 km aprox.")
    hours: int = Field(72, ge=6, le=168, description="Ventana de horas a sumar (default 72)")
    clear_previous: bool = Field(True, description="Borra pronóstico previo en la ventana antes de insertar")

def frange(a: float, b: float, step: float):
    vals = []
    x = a
    while x <= b + 1e-9:  # incluir borde superior
        vals.append(round(x, 6))
        x += step
    return vals

@router.post("/openmeteo")
def load_openmeteo(req: OpenMeteoReq):
    try:
        # 1) Parse bbox
        try:
            minx, miny, maxx, maxy = [float(x) for x in req.bbox.split(",")]
        except Exception:
            raise HTTPException(status_code=400, detail="bbox debe ser 'minx,miny,maxx,maxy'")

        # 2) Rejilla (centroides)
        lons = frange(minx, maxx, req.step_deg)
        lats = frange(miny, maxy, req.step_deg)
        if len(lons) * len(lats) > 200:
            raise HTTPException(status_code=400, detail="Rejilla muy grande. Usa step_deg más grande (ej. 0.08).")

        # Ventana naive (UTC) para evitar choque con tz en la columna TIMESTAMP
        t0 = datetime.utcnow()
        t1 = t0 + timedelta(hours=req.hours)

        inserted = 0
        sql_insert = text("""
            INSERT INTO precip_forecast (ts, mm, geom)
            VALUES (:ts, :mm, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
        """)

        # 3) Limpiar ventana previa (opcional)
        if req.clear_previous:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM precip_forecast WHERE ts BETWEEN :t0 AND :t1"),
                    {"t0": t0, "t1": t1}
                )

        # 4) Por cada punto, pedir Open-Meteo, sumar mm y guardar celda
        with engine.begin() as conn:
            for lat in lats:
                for lon in lons:
                    url = "https://api.open-meteo.com/v1/forecast"
                    params = {
                        "latitude": lat,
                        "longitude": lon,
                        "hourly": "precipitation",
                        "forecast_days": 7,
                        "timezone": "UTC"
                    }
                    try:
                        r = requests.get(url, params=params, timeout=15)
                        r.raise_for_status()
                        data = r.json()
                    except Exception as e:
                        print(f"Open-Meteo fallo en {lat},{lon}: {e}")
                        continue

                    times = data.get("hourly", {}).get("time", [])
                    precs = data.get("hourly", {}).get("precipitation", [])
                    if not times or not precs or len(times) != len(precs):
                        continue

                    total_mm = 0.0
                    for iso, mm in zip(times, precs):
                        try:
                            # parse a aware y luego quita tz -> naive
                            ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
                        except Exception:
                            continue
                        if t0 <= ts <= t1:
                            total_mm += (mm or 0.0)

                    # Polígono cuadrado alrededor del punto (± step/2)
                    half = req.step_deg / 2.0
                    poly = {
                        "type": "Polygon",
                        "coordinates": [[
                            [lon - half, lat - half],
                            [lon + half, lat - half],
                            [lon + half, lat + half],
                            [lon - half, lat + half],
                            [lon - half, lat - half],
                        ]]
                    }

                    if total_mm <= 0:
                        continue

                    conn.execute(sql_insert, {
                        "ts": t0,                 # marca de corrida (naive UTC)
                        "mm": float(total_mm),
                        "geom": json.dumps(poly)
                    })
                    inserted += 1

        return {
            "ok": True,
            "inserted": inserted,
            "window_utc": {"from": t0.isoformat(), "to": t1.isoformat()},
            "grid": {"nx": len(lons), "ny": len(lats), "step_deg": req.step_deg}
        }

    except Exception as e:
        # <-- este except CIERRA el try de arriba, al MISMO nivel de indentación
        raise HTTPException(status_code=500, detail=f"openmeteo falló: {e!r}")
