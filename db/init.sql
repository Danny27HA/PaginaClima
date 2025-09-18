CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS calles (
  id SERIAL PRIMARY KEY,
  nombre TEXT,
  alcaldia TEXT,
  geom geometry(LineString, 4326)
);

CREATE TABLE IF NOT EXISTS flood_polygons (
  id SERIAL PRIMARY KEY,
  fuente TEXT,
  fecha DATE,
  geom geometry(Polygon, 4326)
);

CREATE TABLE IF NOT EXISTS precip_forecast (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMP NOT NULL,
  mm FLOAT,
  geom geometry(Polygon, 4326)
);

CREATE TABLE IF NOT EXISTS incidents (
  id BIGSERIAL PRIMARY KEY,
  fecha TIMESTAMP,
  tipo TEXT,
  geom geometry(Point, 4326)
);

CREATE TABLE IF NOT EXISTS risk_scores (
  run_id TEXT,
  calle_id INT REFERENCES calles(id),
  score NUMERIC,
  nivel TEXT,
  p72_mm NUMERIC,
  justificacion TEXT,
  PRIMARY KEY (run_id, calle_id)
);
