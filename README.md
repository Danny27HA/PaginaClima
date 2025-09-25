CDMX Flood – Pronóstico, riesgo por calle y chat IA

Mapa interactivo para visualizar riesgo de inundación por tramo de calle en la CDMX a 72 h, usando:

FastAPI (backend)

PostgreSQL + PostGIS (geodatos)

Leaflet (frontend)

Open-Meteo (pronóstico gratuito)

OpenRouter (DeepSeek free) para chat IA opcional

Proyecto educativo. No usar para emergencias reales.

Demo local (resumen rápido)

Clona y entra al proyecto:

git clone https://github.com/Danny27HA/PaginaClima.git
cd PaginaClima


Crea y activa entorno Python:

# Windows (PowerShell)
python -m venv api\.venv
api\.venv\Scripts\activate

# macOS/Linux
python3 -m venv api/.venv
source api/.venv/bin/activate


Instala dependencias:

pip install -r requirements.txt


Crea base de datos (PostgreSQL + PostGIS) y variables de entorno (ver secciones abajo).
Copia .env.example a .env y edítalo.

Levanta el backend:

cd api
uvicorn api.main:app --reload


Abre la documentación: http://127.0.0.1:8000/docs

Carga pronóstico de prueba (en /docs → POST /forecast/openmeteo):

{
  "bbox": "-99.36,19.18,-98.94,19.59",
  "step_deg": 0.04,
  "hours": 72,
  "clear_previous": true
}


Sirve el frontend (mapa):

# desde la carpeta raíz
cd web
python -m http.server 8080


Abre: http://127.0.0.1:8080

Requisitos

Python 3.10+

PostgreSQL 14+ con PostGIS

Git

(Opcional) VS Code con extensión Python

(Opcional) Clave OpenRouter para el chat IA

Configuración de la base de datos

Instala PostgreSQL + PostGIS.

Crea usuario y base:

-- En psql como superusuario (postgres)
CREATE USER flooduser WITH PASSWORD '1234' LOGIN;
CREATE DATABASE flooddb OWNER flooduser;
\c flooddb
CREATE EXTENSION IF NOT EXISTS postgis;


Crea tablas mínimas (si no existen). Entra a psql con tu usuario:

psql -U flooduser -h 127.0.0.1 -d flooddb


Luego ejecuta (si ya corriste el backend una vez, esto lo crea automáticamente desde db/init.sql; si no, puedes usar este esquema básico):

-- Calles (líneas)
CREATE TABLE IF NOT EXISTS calles (
  id SERIAL PRIMARY KEY,
  nombre TEXT,
  alcaldia TEXT,
  geom geometry(LineString,4326)
);
CREATE INDEX IF NOT EXISTS idx_calles_geom ON calles USING GIST (geom);

-- Pronóstico (polígonos/celdas)
CREATE TABLE IF NOT EXISTS precip_forecast (
  id SERIAL PRIMARY KEY,
  ts timestamp without time zone NOT NULL,
  mm double precision NOT NULL,
  geom geometry(Polygon,4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_precip_geom ON precip_forecast USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_precip_ts   ON precip_forecast (ts);

-- Polígonos de riesgo (opcional, puedes dejarlo vacío)
CREATE TABLE IF NOT EXISTS flood_polygons (
  id SERIAL PRIMARY KEY,
  fuente TEXT,
  fecha date,
  geom geometry(MultiPolygon,4326)
);
CREATE INDEX IF NOT EXISTS idx_flood_geom ON flood_polygons USING GIST (geom);

-- Alcaldías (opcional para etiquetar)
CREATE TABLE IF NOT EXISTS alcaldias (
  id SERIAL PRIMARY KEY,
  nombre TEXT,
  geom geometry(MultiPolygon,4326)
);
CREATE INDEX IF NOT EXISTS idx_alcaldias_geom ON alcaldias USING GIST (geom);


Calles: puedes cargar OpenStreetMap por batch o empezar con unas líneas de prueba.
flood_polygons: si no tienes datos oficiales, el sistema sigue funcionando (hazard=0).
alcaldias: si cargas polígonos oficiales, el backend etiqueta mejor.

Variables de entorno

Crea un archivo .env en la carpeta api/ (o raíz si prefieres) con:

POSTGRES_URL=postgresql+psycopg://flooduser:1234@127.0.0.1:5432/flooddb
APP_ENV=dev
API_BASE_URL=http://127.0.0.1:8000

# Para IA (opcional, si quieres chat)
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxx


No subas tu API key al repo público.

Ejecutar backend

Desde la raíz o api/ con el venv activo:

uvicorn api.main:app --reload


Docs: http://127.0.0.1:8000/docs

Endpoints clave:

GET /system/db – estado de conexión a DB

POST /forecast – carga manual de celdas (GeoJSON + mm)

POST /forecast/openmeteo – descarga/suma y guarda rejilla 72 h

GET /forecast/summary – resumen ventana 72 h

GET /score – ranking por calle (JSON)

GET /score/geojson – ranking por calle (GeoJSON; se usa en el mapa)

POST /chat – chat IA con intents (usa datos del backend cuando corresponde)

Parámetros útiles en /score y /score/geojson

top_k — número de tramos a devolver (ej. 3000 o 5000)

bbox — recorte espacial minx,miny,maxx,maxy (WGS84)

tolerance_m — buffer en metros para cruzar líneas ↔ celdas

use_hazard — usa/no usa polígonos de riesgo (si no tienes datos: false)

min_mm — filtra calles con lluvia acumulada mínima

only_cdmx — si está implementado, limita a alcaldías CDMX (true/false)

mm_ref — referencia de mm para el score (normalización)

Frontend (Leaflet)

El mapa está en web/index.html. Para evitar problemas de CORS, sírvelo con un servidor local:

cd web
python -m http.server 8080


Abre: http://127.0.0.1:8080

El archivo ya trae:

leyenda de colores (Alto/Medio/Bajo),

llamadas a /score/geojson con parámetros,

cajón de chat a la derecha (usa /chat).

Si cambias parámetros (ej. only_cdmx=true, mm_ref=100), edita el fetch(...) dentro de index.html.

Chat IA (OpenRouter / DeepSeek)

El endpoint es POST /chat y trata de resolver primero con datos locales (intents):

“reporte 72h / resumen”

“calles con nivel alto/medio/bajo en <alcaldía>”

“lluvia/p72 en <alcaldía>”

Si no hay intent que aplique, hace fallback a OpenRouter (DeepSeek free) si has puesto OPENROUTER_API_KEY.

Preguntas de ejemplo:

“reporte 72h”

“calles con nivel alto en Iztapalapa”

“lluvia acumulada en Gustavo A. Madero”

“top 3 alcaldías con mayor riesgo”

“¿qué significa p72_mm?”

Flujo típico (local)

Levanta el backend (uvicorn).

Carga un pronóstico en /docs → POST /forecast/openmeteo:

{
  "bbox": "-99.36,19.18,-98.94,19.59",
  "step_deg": 0.04,
  "hours": 72,
  "clear_previous": true
}


Abre el mapa en web/index.html (sirve en 8080).

Usa el chat (preguntas con intents) o consume endpoints.
