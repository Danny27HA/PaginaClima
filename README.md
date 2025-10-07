# CDMX Flood — Pronóstico de lluvia y riesgo por calle (72h)

**CDMX Flood** es una aplicación que combina pronóstico de precipitación a 72 horas con capas geoespaciales (calles y polígonos de riesgo) para estimar un **nivel de riesgo por tramo de calle** dentro de la Ciudad de México. Incluye:
- **API** (FastAPI + PostGIS) para ingestión de pronóstico, cálculos y consulta.
- **Mapa web** (Leaflet) que pinta las calles por **nivel de riesgo** (Bajo/Medio/Alto).
- **Chat asistente** que responde preguntas sobre la situación (p. ej. “calles con nivel alto en Iztapalapa”).

---

## ¿Qué hace la app?

1. **Descarga y resume el pronóstico de lluvia** (Open-Meteo) sobre una cuadrícula que cubre CDMX.
2. **Convierte ese pronóstico en celdas GeoJSON** (polígonos) con la lluvia acumulada a 72h (*p72_mm*).
3. **Cruza esas celdas con la red de calles** para calcular, por cada tramo, cuánta lluvia le “cae encima”.
4. **(Opcional) Considera polígonos de riesgo** (histórico/atlas) para elevar el score en zonas susceptibles.
5. **Calcula un “score” de riesgo** por calle y lo clasifica como **Bajo / Medio / Alto**.
6. **Expone endpoints** para consultar esos resultados como listas o **GeoJSON**.
7. **Dibuja el mapa** con colores por nivel y popups con la información clave.
8. **Permite consultas en lenguaje natural** (chat), priorizando datos del backend; si no hay datos, responde con IA.

---

## Cómo funciona (arquitectura)

### 1) Backend (FastAPI + PostGIS)
- **Base de datos**: PostgreSQL/PostGIS con tablas:
  - `calles` (LineString, una por tramo).
  - `precip_forecast` (polígonos/celdas con mm acumulados y `ts`).
  - `flood_polygons` (opcional, zonas históricas/susceptibles).
- **Endoints principales**:
  - `POST /forecast/openmeteo`  
    Descarga precipitación horaria (Open-Meteo), **suma 72h**, y guarda celdas (polígonos) con su `mm`.  
    Parámetros clave:
    - `bbox` (WGS84): área a cubrir.
    - `step_deg`: tamaño de celda (p. ej. `0.04` ≈ 4 km aprox).
    - `hours`: por defecto 72.
    - `clear_previous`: borrar celdas previas del mismo rango temporal.
  - `GET /forecast/summary`  
    Resumen de cuántas celdas hay y la suma total de mm en un rango.
  - `GET /score`  
    Devuelve el **ranking de calles** (solo datos tabulares).
  - `GET /score/geojson`  
    Devuelve **FeatureCollection** con las calles y propiedades:
    - `nombre`, `alcaldia`
    - `p72_mm` (lluvia 72h acumulada)
    - `hazard` (0/1)
    - `score` y `nivel` (**Bajo/Medio/Alto**)
    
  Filtros y parámetros útiles:
  - `bbox`: recorta el cálculo/consulta a un área.
  - `tolerance_m`: buffer en metros para cruzar calles con celdas.
  - `use_hazard`: si `true`, incorpora intersección con `flood_polygons`.
  - `min_mm`: filtra calles con lluvia mínima.
  - `only_cdmx`: limita resultados a CDMX (si la tabla de alcaldías está cargada).
  - `mm_ref`: calibra qué tanto “pesa” la lluvia en el score.

- **Score (idea general)**  
  Se calcula como combinación de:
  - **Lluvia 72h (p72_mm)**, normalizada contra `mm_ref` (ej. 80–100 mm).
  - **Hazard** (1 si la calle toca un polígono de riesgo; 0 si no).
  
  El “nivel” se asigna por umbrales de `score`:
  - **Alto** (rojo)
  - **Medio** (naranja)
  - **Bajo** (verde)

### 2) Frontend (web/index.html + Leaflet)
- Mapa centrado en CDMX.
- Capa de **calles** pintada por `nivel`:
  - **Rojo** = Alto  
  - **Naranja** = Medio  
  - **Verde** = Bajo
- Pop-ups al hacer click con:
  - Nombre de la calle
  - Alcaldía
  - Lluvia acumulada 72h (`p72_mm`)
  - `hazard` y `score`

La URL que consume el mapa luce así (ejemplo):
/score/geojson?hours=72&top_k=50000&bbox=-99.36,19.18,-98.94,19.59
&tolerance_m=10&use_hazard=true&min_mm=0&only_cdmx=true&mm_ref=100

markdown
Copiar código
> Cambiando esos parámetros (en el script del HTML) puedes **acotar el área**, **ajustar la sensibilidad** y **filtrar**.

### 3) Chat (API de IA + reglas)
- **Router**: `POST /chat` con JSON `{ "question": "..." }`.
- **Lógica**: intenta **resolver con datos del backend** (resumen, calles por alcaldía, promedio de p72 por alcaldía, top de riesgo por alcaldía, etc.).  
- Si no aplica, cae a un **modelo de IA** (por OpenRouter/DeepSeek u otro).
- En el **HTML** hay una cajita lateral para chatear, con indicador de “escribiendo…”.

---

## Flujo típico de uso

1. **Cargar pronóstico** (una sola vez al arrancar o manual):  
   `POST /forecast/openmeteo` con `bbox` de CDMX, `hours=72`, `step_deg` y `clear_previous=true`.
2. **Explorar en el mapa**: abrir `web/index.html` en el navegador.  
   (El mapa consulta `/score/geojson` y pinta las calles).
3. **Consultar por API o chat**:  
   - `GET /forecast/summary` para resumen 72h.  
   - `GET /score` para tabla de top calles.  
   - Chat: “Calles con nivel alto en X alcaldía”, “Promedio p72 en X”, “Top alcaldías con mayor riesgo”, etc.

---

## Qué significan las métricas

- **p72_mm**: precipitación acumulada (en **milímetros**) prevista para las **próximas 72 horas** en el entorno de una calle.
- **hazard**: 1 si la calle intersecta un polígono de riesgo (cuando `use_hazard=true`), 0 si no.
- **score**: combinación de p72_mm (normalizada con `mm_ref`) y `hazard`.
- **nivel**:
  - **Bajo** (verde), **Medio** (naranja), **Alto** (rojo) — según umbrales de `score`.

---

## Decisiones de diseño

- **PostGIS** para todos los cruces espaciales eficientes (índices, buffers, intersecciones).
- **Cálculo por “último pronóstico”**: los endpoints de score usan la corrida más reciente para respuestas rápidas y consistentes.
- **Parámetros abiertos** (`mm_ref`, `tolerance_m`, `min_mm`, `bbox`) para adaptar la sensibilidad y el área.

---

## Limitaciones conocidas

- La calidad del resultado depende del **pronóstico** (Open-Meteo) y de la **granularidad** (`step_deg`).
- `flood_polygons` es opcional; si está vacío, `hazard` aporta 0 (y todo depende de la lluvia).
- El **chat** no es un sistema de emergencias; las respuestas son orientativas.

---

## Aviso y uso responsable

Este proyecto es con fines **educativos**. No sustituye información oficial ni alertas de **Protección Civil**. Para decisiones críticas, consulta fuentes oficiales (SACMEX, SGIRPC, CONAGUA).

---

## Créditos

- API y cálculo: **FastAPI + SQLAlchemy + PostGIS**  
- Mapa: **Leaflet + OpenStreetMap**  
- Pronóstico: **Open-Meteo**  
- Chat: **OpenRouter / DeepSeek** (o el proveedor gratuito configurado)
