# tools/load_alcaldias_geojson.py
import json
from datetime import date
from sqlalchemy import create_engine, text

DSN = "postgresql+psycopg://flooduser:1234@127.0.0.1:5432/flooddb"
GEOJSON_PATH = "data/alcaldias_cdmx.json"

# Intenta varios nombres de campo comunes para el nombre de alcaldía
NAME_FIELDS = ["NOMGEO", "NOM_ALC", "alcaldia", "ALCALDIA", "name", "NOM_MUN", "nomgeo"]

engine = create_engine(DSN)

def get_name(props: dict) -> str:
    for k in NAME_FIELDS:
        if k in props and props[k]:
            return str(props[k])
    return "(sin nombre)"

def main():
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        gj = json.load(f)
    feats = gj["features"]
    inserted = 0
    sql = text("""
        INSERT INTO alcaldias (nombre, geom)
        VALUES (:nombre, ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))
    """)
    with engine.begin() as conn:
        for ft in feats:
            geom = json.dumps(ft["geometry"])
            nombre = get_name(ft.get("properties", {}) or {})
            conn.execute(sql, {"nombre": nombre, "geom": geom})
            inserted += 1
    print(f"[ok] Insertadas {inserted} alcaldías.")

if __name__ == "__main__":
    main()
