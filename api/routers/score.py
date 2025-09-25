from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import text
from ..db import engine

router = APIRouter(prefix="/score", tags=["score"])

class ScoreRow(BaseModel):
    calle: str
    alcaldia: Optional[str]
    p72_mm: float
    hazard: float
    score: float
    nivel: str

class ScoreResponse(BaseModel):
    run_window_utc_from: str
    run_window_utc_to: str
    bbox: Optional[str]
    top_k: int
    rows: List[ScoreRow]

# ====================== /score ======================
@router.get("", response_model=ScoreResponse)
def score_flood(
    hours: int = Query(72, ge=1, le=168),
    top_k: int = Query(10, ge=1, le=50000),
    bbox: Optional[str] = Query(None, description="minx,miny,maxx,maxy (WGS84)"),
    tolerance_m: float = Query(0, ge=0, le=50, description="Buffer en metros"),
    use_hazard: bool = Query(True, description="Si False, ignora hazard"),
    min_mm: float = Query(0.0, ge=0.0, description="Filtra calles con lluvia acumulada mínima"),
    only_cdmx: bool = Query(False, description="Si True, solo calles dentro de alcaldías CDMX"),
    mm_ref: float = Query(80.0, gt=0, description="mm de referencia para normalizar (default 80)")
):
    """
    Puntaje por calle usando el último pronóstico cargado (MAX(ts)).
    score = 0.3*hazard + 0.7*min(1, p72/mm_ref)
    nivel: Alto (>=0.70), Medio (>=0.30), Bajo (<0.30)
    """
    t0 = datetime.utcnow()
    t1 = t0 + timedelta(hours=hours)

    where_extra = ""
    params = {"top_k": top_k, "tol_m": tolerance_m, "min_mm": min_mm, "mm_ref": mm_ref}

    if bbox:
        try:
            minx, miny, maxx, maxy = [float(x) for x in bbox.split(",")]
        except Exception:
            raise HTTPException(status_code=400, detail="bbox debe ser 'minx,miny,maxx,maxy'")
        where_extra += " AND ST_Intersects(c.geom, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326))"
        params.update({"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})

    if only_cdmx:
        where_extra += " AND EXISTS (SELECT 1 FROM alcaldias a WHERE ST_Intersects(c.geom, a.geom))"

    # Prefiltro con índice + distancia precisa si hay tolerancia
    metric_join = (
        "(c.geom && p.geom AND ST_DWithin(ST_Transform(c.geom,3857), ST_Transform(p.geom,3857), :tol_m))"
        if tolerance_m > 0 else
        "(c.geom && p.geom AND ST_Intersects(c.geom, p.geom))"
    )

    # Hazard usando EXISTS (incluye tabla f y prefiltro &&)
    if use_hazard:
        hazard_expr = (
            "EXISTS (SELECT 1 FROM flood_polygons f "
            "        WHERE c.geom && f.geom "
            "          AND ST_DWithin(ST_Transform(c.geom,3857), ST_Transform(f.geom,3857), :tol_m))"
            if tolerance_m > 0 else
            "EXISTS (SELECT 1 FROM flood_polygons f "
            "        WHERE c.geom && f.geom "
            "          AND ST_Intersects(c.geom, f.geom))"
        )
    else:
        hazard_expr = "FALSE"

    having_clause = "HAVING COALESCE(SUM(p.mm),0) >= :min_mm"

    sql = text(f"""
        WITH last AS (
            SELECT MAX(ts) AS ts_last FROM precip_forecast
        ),
        p AS (
            SELECT id, mm, geom
            FROM precip_forecast, last
            WHERE precip_forecast.ts = last.ts_last
        ),
        agg AS (
            SELECT
                c.id AS calle_id,
                c.nombre AS calle,
                c.alcaldia AS alcaldia,
                COALESCE(SUM(p.mm), 0) AS p72_mm,
                CASE WHEN {hazard_expr} THEN 1.0 ELSE 0.0 END AS hazard
            FROM calles c
            LEFT JOIN p ON {metric_join}
            WHERE 1=1 {where_extra}
            GROUP BY c.id, c.nombre, c.alcaldia
            {having_clause}
        ),
        scored AS (
            SELECT
                calle,
                alcaldia,
                p72_mm,
                hazard,
                0.3*hazard + 0.7*LEAST(1, p72_mm/:mm_ref) AS score
            FROM agg
        )
        SELECT
            calle, alcaldia, p72_mm, hazard, score,
            CASE
                WHEN score >= 0.70 THEN 'Alto'
                WHEN score >= 0.30 THEN 'Medio'
                ELSE 'Bajo'
            END AS nivel
        FROM scored
        ORDER BY score DESC
        LIMIT :top_k
    """)

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]

    return ScoreResponse(
        run_window_utc_from=t0.isoformat(),
        run_window_utc_to=t1.isoformat(),
        bbox=bbox,
        top_k=top_k,
        rows=[ScoreRow(**r) for r in rows]
    )

# ====================== /score/geojson ======================
@router.get("/geojson")
def score_geojson(
    hours: int = Query(72, ge=1, le=168),
    top_k: int = Query(50, ge=1, le=50000),
    bbox: Optional[str] = Query(None, description="minx,miny,maxx,maxy (WGS84)"),
    tolerance_m: float = Query(0, ge=0, le=50),
    use_hazard: bool = Query(True, description="Si False, ignora hazard"),
    min_mm: float = Query(0.0, ge=0.0, description="Filtra calles con lluvia acumulada mínima"),
    only_cdmx: bool = Query(False, description="Si True, solo calles dentro de alcaldías CDMX"),
    mm_ref: float = Query(80.0, gt=0, description="mm de referencia para normalizar (default 80)")
):
    where_extra = ""
    params = {"top_k": top_k, "tol_m": tolerance_m, "min_mm": min_mm, "mm_ref": mm_ref}

    if bbox:
        try:
            minx, miny, maxx, maxy = [float(x) for x in bbox.split(",")]
        except Exception:
            raise HTTPException(status_code=400, detail="bbox debe ser 'minx,miny,maxx,maxy'")
        where_extra += " AND ST_Intersects(c.geom, ST_MakeEnvelope(:minx,:miny,:maxx,:maxy,4326))"
        params.update({"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})

    if only_cdmx:
        where_extra += " AND EXISTS (SELECT 1 FROM alcaldias a WHERE ST_Intersects(c.geom, a.geom))"

    metric_join = (
        "(c.geom && p.geom AND ST_DWithin(ST_Transform(c.geom,3857), ST_Transform(p.geom,3857), :tol_m))"
        if tolerance_m > 0 else
        "(c.geom && p.geom AND ST_Intersects(c.geom, p.geom))"
    )

    if use_hazard:
        hazard_expr = (
            "EXISTS (SELECT 1 FROM flood_polygons f "
            "        WHERE c.geom && f.geom "
            "          AND ST_DWithin(ST_Transform(c.geom,3857), ST_Transform(f.geom,3857), :tol_m))"
            if tolerance_m > 0 else
            "EXISTS (SELECT 1 FROM flood_polygons f "
            "        WHERE c.geom && f.geom "
            "          AND ST_Intersects(c.geom, f.geom))"
        )
    else:
        hazard_expr = "FALSE"

    having_clause = "HAVING COALESCE(SUM(p.mm),0) >= :min_mm"

    sql = text(f"""
        WITH last AS (
            SELECT MAX(ts) AS ts_last FROM precip_forecast
        ),
        p AS (
            SELECT id, mm, geom
            FROM precip_forecast, last
            WHERE precip_forecast.ts = last.ts_last
        ),
        agg AS (
            SELECT
                c.id, c.nombre, c.alcaldia, c.geom,
                COALESCE(SUM(p.mm), 0) AS p72_mm,
                CASE WHEN {hazard_expr} THEN 1.0 ELSE 0.0 END AS hazard
            FROM calles c
            LEFT JOIN p ON {metric_join}
            WHERE 1=1 {where_extra}
            GROUP BY c.id, c.nombre, c.alcaldia, c.geom
            {having_clause}
        ),
        scored AS (
            SELECT
                id, nombre, alcaldia, geom, p72_mm, hazard,
                0.3*hazard + 0.7*LEAST(1, p72_mm/:mm_ref) AS score
            FROM agg
        )
        SELECT
            nombre, alcaldia, p72_mm, hazard, score,
            CASE WHEN score >= 0.70 THEN 'Alto'
                 WHEN score >= 0.30 THEN 'Medio'
                 ELSE 'Bajo' END AS nivel,
            ST_AsGeoJSON(geom)::json AS geom_json
        FROM scored
        ORDER BY score DESC
        LIMIT :top_k
    """)

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]

    features = []
    for r in rows:
        geom = r.pop("geom_json")
        features.append({"type": "Feature", "geometry": geom, "properties": r})

    return {"type": "FeatureCollection", "features": features}
