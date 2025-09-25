# tools/load_flood_polygons_geojson.py
import json
from datetime import date
from sqlalchemy import create_engine, text

DSN = "postgresql+psycopg://flooduser:1234@127.0.0.1:5432/flooddb"
GEOJSON_PATH = "data/flood_zones.geojson"  # ajusta si usas otro nombre
FUENTE = "sgirpc"
BUFFER_M = 30  # radio para puntos (m) -> polígono

engine = create_engine(DSN)

def guess_srid_from_coords(coords):
    """Si los valores son muy grandes, asumimos UTM 14N (32614). Si parecen grados, 4326."""
    try:
        if isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float)):
            x, y = coords[0], coords[1]
        else:
            # Polygon: coords[0][0] = [x,y]
            x, y = coords[0][0]
    except Exception:
        return 4326
    if abs(x) > 180 or abs(y) > 90:
        return 32614  # UTM zona 14N (CDMX)
    return 4326

def main():
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        gj = json.load(f)

    feats = gj["features"]
    today = date.today().isoformat()
    inserted = 0

    sql_poly = text("""
        INSERT INTO flood_polygons (fuente, fecha, geom)
        VALUES (
            :fuente, :fecha,
            ST_Transform(
                ST_SetSRID(ST_GeomFromGeoJSON(:geom), :srid_in),
                4326
            )
        )
    """)
    sql_point = text("""
        INSERT INTO flood_polygons (fuente, fecha, geom)
        VALUES (
            :fuente, :fecha,
            ST_Transform(
                ST_Buffer(
                    ST_Transform(
                        ST_SetSRID(ST_GeomFromText(:wkt_point, :srid_in), :srid_in),
                        3857
                    ),
                    :buf_m
                ),
                4326
            )
        )
    """)

    with engine.begin() as conn:
        for ft in feats:
            geom = ft.get("geometry")
            if not geom:
                continue
            gtype = geom.get("type")
            coords = geom.get("coordinates")
            srid_in = guess_srid_from_coords(coords)

            if gtype in ("Polygon", "MultiPolygon"):
                conn.execute(sql_poly, {
                    "fuente": FUENTE,
                    "fecha": today,
                    "geom": json.dumps(geom),
                    "srid_in": srid_in
                })
                inserted += 1

            elif gtype == "Point":
                # construir WKT POINT(x y)
                x, y = coords
                wkt = f"POINT({x} {y})"
                conn.execute(sql_point, {
                    "fuente": FUENTE,
                    "fecha": today,
                    "wkt_point": wkt,
                    "srid_in": srid_in,
                    "buf_m": BUFFER_M
                })
                inserted += 1

            elif gtype == "MultiPoint" or gtype == "LineString" or gtype == "MultiLineString":
                # Trátalos como puntos centroides o ignóralos; aquí los ignoramos
                continue

    print(f"[ok] Insertados {inserted} registros en flood_polygons (reproyectados a EPSG:4326).")

if __name__ == "__main__":
    main()
