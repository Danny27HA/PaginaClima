import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg://flooduser:floodpass@127.0.0.1:5432/flooddb"
)

engine = create_engine(POSTGRES_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def db_version():
    try:
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version();")).scalar_one()
            try:
                postgis = conn.execute(text("SELECT postgis_version();")).scalar_one()
            except Exception as e:
                postgis = f"PostGIS no disponible: {e}"
            return {"postgres": ver, "postgis": postgis, "dsn": POSTGRES_URL}
    except Exception as e:
        # imprime en consola y devuelve detalle útil
        print("DB ERROR:", repr(e))
        return {"error": str(e), "dsn": POSTGRES_URL}
