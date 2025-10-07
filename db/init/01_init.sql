-- Extensiones
CREATE EXTENSION IF NOT EXISTS postgis;

-- Calles
CREATE TABLE IF NOT EXISTS calles (
  id SERIAL PRIMARY KEY,
  nombre TEXT,
  alcaldia TEXT,
  geom geometry(MultiLineString, 4326)
);
CREATE INDEX IF NOT EXISTS idx_calles_geom ON calles USING GIST (geom);

-- Pronóstico de precipitación (polígonos/celdas)
CREATE TABLE IF NOT EXISTS precip_forecast (
  id SERIAL PRIMARY KEY,
  ts TIMESTAMP NOT NULL,      -- marca de corrida (UTC naive)
  mm DOUBLE PRECISION NOT NULL DEFAULT 0,
  geom geometry(Polygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_precip_geom ON precip_forecast USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_precip_ts ON precip_forecast (ts);

-- Alcaldías (para enriquecer nombres)
CREATE TABLE IF NOT EXISTS alcaldias (
  id SERIAL PRIMARY KEY,
  nombre TEXT,
  geom geometry(MultiPolygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_alcaldias_geom ON alcaldias USING GIST (geom);

-- Polígonos de riesgo (opcional)
CREATE TABLE IF NOT EXISTS flood_polygons (
  id SERIAL PRIMARY KEY,
  fuente TEXT,
  fecha DATE,
  geom geometry(Polygon, 4326)
);
CREATE INDEX IF NOT EXISTS idx_flood_geom ON flood_polygons USING GIST (geom);
