# tools/load_osm_roads.py
import json
import time
import requests
from sqlalchemy import create_engine, text

# ---- Configura tu conexión (igual que en system/db) ----
DSN = "postgresql+psycopg://flooduser:1234@127.0.0.1:5432/flooddb"

# ---- BBox CDMX (sur, oeste, norte, este) para Overpass ----
# OJO: Overpass usa orden: south,west,north,east
BBOX = (19.18, -99.36, 19.59, -98.94)

# Qué tipos de vialidades descargar (comienza simple; luego ampliamos)
HIGHWAYS = "^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street)$"
# Si quieres TODO (incluye calles pequeñas): "^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service)$"

# Límite de elementos por tanda (para no abusar del API)
MAX_WAYS = 20000

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

def build_query(bbox, highways_regex):
    s, w, n, e = bbox
    return f"""
    [out:json][timeout:90];
    (
      way["highway"]["highway"~"{highways_regex}"]({s},{w},{n},{e});
    );
    out geom;
    """

def fetch_ways():
    q = build_query(BBOX, HIGHWAYS)
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=120)
    r.raise_for_status()
    data = r.json()
    elems = [el for el in data.get("elements", []) if el.get("type") == "way" and "geometry" in el]
    if len(elems) > MAX_WAYS:
        print(f"[warn] {len(elems)} ways encontrados; cortando a {MAX_WAYS}")
        elems = elems[:MAX_WAYS]
    return elems

def way_to_geojson_line(way):
    coords = [[pt["lon"], pt["lat"]] for pt in way["geometry"]]
    # Evitar errores con líneas con menos de 2 puntos
    if len(coords) < 2:
        return None
    return {"type": "LineString", "coordinates": coords}

def main():
    print("[info] Descargando vías desde Overpass…")
    ways = fetch_ways()
    print(f"[info] Recibidos {len(ways)} ways")

    engine = create_engine(DSN)
    inserted = 0

    sql_insert = text("""
        INSERT INTO calles (nombre, alcaldia, geom)
        VALUES (:nombre, NULL, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
        ON CONFLICT DO NOTHING
    """)

    with engine.begin() as conn:
        for i, w in enumerate(ways, 1):
            geom = way_to_geojson_line(w)
            if not geom:
                continue
            nombre = (w.get("tags", {}) or {}).get("name") or "(sin nombre)"
            try:
                conn.execute(sql_insert, {"nombre": nombre, "geom": json.dumps(geom)})
                inserted += 1
            except Exception as e:
                # Si alguna geometría da error, seguimos con la siguiente
                print(f"[warn] way {w.get('id')} falló: {e}")
            if i % 500 == 0:
                print(f"[info] procesados {i}/{len(ways)}…")
                time.sleep(0.5)  # pequeña pausa de buena conducta

    print(f"[ok] Insertadas {inserted} calles en PostGIS.")

if __name__ == "__main__":
    main()
