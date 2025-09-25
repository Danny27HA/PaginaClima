# api/routers/chat.py
import os
import re
import json
import requests
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/chat", tags=["chat"])

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

class ChatReq(BaseModel):
    question: Optional[str] = None
    q: Optional[str] = None  # compat

# ---------- helpers HTTP ----------
def _http_get(path: str, params: Dict[str, Any]) -> Any:
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

# ---------- utilidades formato ----------
def _fmt_list(rows: List[Dict[str, Any]], maxn=10) -> str:
    out = []
    for r in rows[:maxn]:
        calle = r.get("calle") or r.get("nombre") or "(sin nombre)"
        alc   = r.get("alcaldia") or "-"
        p72   = float(r.get("p72_mm") or 0.0)
        niv   = r.get("nivel") or "-"
        out.append(f"• {calle} — {alc} | p72={p72:.1f} mm | {niv}")
    return "\n".join(out) if out else "(sin resultados)"

def _clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).title()

# ---------- LLM “parafraseo con hechos” ----------
def _llm_with_facts(task: str, facts: Dict[str, Any], fallback_text: str) -> str:
    """
    Pide a la IA redactar en español, natural y breve, usando SOLO los datos de 'facts'.
    Si no hay API key o falla, se regresa fallback_text.
    """
    if not OPENROUTER_API_KEY:
        return fallback_text

    system = (
        "Eres un asistente que redacta respuestas claras y naturales EN ESPAÑOL "
        "USANDO EXCLUSIVAMENTE los datos proporcionados en 'FACTS'. "
        "No inventes nada. Si un dato no está, no lo menciones. "
        "Evita jerga técnica innecesaria. "
        "Sé breve, directo y útil para un ciudadano."
    )
    user = (
        f"TAREA: {task}\n\n"
        f"FACTS (JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Instrucciones de estilo:\n"
        "- 1–3 párrafos cortos o viñetas breves.\n"
        "- Di las cifras con unidades (mm) cuando apliquen.\n"
        "- No añadas fuentes ni consejos genéricos.\n"
    )

    try:
        rr = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek/deepseek-chat-v3.1:free",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
            },
            timeout=90,
        )
        rr.raise_for_status()
        data = rr.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return fallback_text

# ---------- REGEX (permisivas) ----------
_re_resumen    = re.compile(r"\b(reporte|resumen|sumario)\b", re.I)
_re_calles_x   = re.compile(
    r"(cu[aá]les\s+son|dime|lista|mu[eé]strame|calles?)?[^a-záéíóúñ]*"
    r"(con|de)?\s*nivel\s+(alto|medio|bajo)[^a-záéíóúñ]+(en|de)\s+([a-záéíóúñ\.\- ]+)",
    re.I
)
_re_lluvia_x   = re.compile(
    r"(promedio|media)?[^a-záéíóúñ]*(lluvia|p72|mm)[^a-záéíóúñ]+(en|de)\s+([a-záéíóúñ\.\- ]+)",
    re.I
)
_re_menos_riesgo = re.compile(
    r"(menos\s+probables|menor\s+riesgo|riesgo\s+bajo|calles\s+con\s+(menor|bajo)\s+riesgo)[^a-záéíóúñ]+(en|de)\s+([a-záéíóúñ\.\- ]+)",
    re.I
)

@router.post("")
def chat(req: ChatReq):
    msg = (req.question or req.q or "").strip()
    if not msg:
        raise HTTPException(status_code=422, detail="Falta 'question' en el JSON.")
    lower = msg.lower()

    # 1) Resumen 72h
    if _re_resumen.search(lower):
        try:
            summ = _http_get("forecast/summary", {"from_hours": 0, "to_hours": 72})
        except Exception as e:
            return {"answer": f"⚠️ No pude leer el resumen 72h: {e}"}
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 500, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception:
            filas = []

        facts = {
            "ventana_utc": summ.get("window_utc", {}),
            "n_celdas": summ.get("n_cells", 0),
            "lluvia_total_mm": float(summ.get("mm_sum", 0.0)),
            "top_calles": filas[:10],
        }
        fallback = (
            f"Resumen 72h: lluvia total {facts['lluvia_total_mm']:.1f} mm, "
            f"celdas={facts['n_celdas']}. Top calles:\n{_fmt_list(facts['top_calles'], 10)}"
        )
        return {"answer": _llm_with_facts("Redacta un resumen claro de la situación en 72h.", facts, fallback)}

    # 2) Calles con nivel (alto|medio|bajo) en <alcaldía>
    m = _re_calles_x.search(lower)
    if m:
        nivel = (m.group(3) or "").strip().lower()
        alc   = _clean_name(m.group(5) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 3000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}
        fil = [r for r in filas
               if (r.get("alcaldia") or "").lower().find(alc.lower()) >= 0
               and (r.get("nivel") or "").lower() == nivel]
        if not fil:
            return {"answer": f"No encontré calles con nivel {nivel} en {alc}."}

        facts = {
            "alcaldia": alc,
            "nivel": nivel,
            "total_tramos": len(fil),
            "ejemplos": fil[:15],
        }
        fallback = f"Calles con nivel {nivel} en {alc}:\n{_fmt_list(fil, 15)}"
        return {"answer": _llm_with_facts("Redacta una respuesta breve con ejemplos.", facts, fallback)}

    # 3) Lluvia/p72/mm en <alcaldía>
    m2 = _re_lluvia_x.search(lower)
    if m2:
        alc = _clean_name(m2.group(4) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 5000, "tolerance_m": 5,
                "use_hazard": False, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}
        sel = [r for r in filas if (r.get("alcaldia") or "").lower().find(alc.lower()) >= 0]
        if not sel:
            return {"answer": f"No tengo datos para {alc} ahora mismo."}
        vals = [float(r.get("p72_mm") or 0.0) for r in sel]
        avg = sum(vals)/len(vals)
        mx  = max(vals)
        mn  = min(vals)

        facts = {
            "alcaldia": alc,
            "n_tramos": len(sel),
            "p72_prom_mm": round(avg, 1),
            "p72_max_mm": round(mx, 1),
            "p72_min_mm": round(mn, 1),
        }
        fallback = (f"Lluvia 72h en {alc} (sobre {facts['n_tramos']} tramos): "
                    f"prom={facts['p72_prom_mm']} mm, máx={facts['p72_max_mm']} mm, mín={facts['p72_min_mm']} mm.")
        return {"answer": _llm_with_facts("Resume en 1–2 frases la lluvia 72h.", facts, fallback)}

    # 4) Menor riesgo en <alcaldía>
    m3 = _re_menos_riesgo.search(lower)
    if m3:
        # puede capturar en grupo 3 o 4 según variante
        alc_raw = m3.group(3) or m3.group(4) or ""
        alc = _clean_name(alc_raw)
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 3000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}
        sel = [r for r in filas if (r.get("alcaldia") or "").lower().find(alc.lower()) >= 0]
        if not sel:
            return {"answer": f"No tengo datos para {alc} ahora mismo."}
        sel_sorted = sorted(sel, key=lambda r: float(r.get("score") or 0.0))

        facts = {
            "alcaldia": alc,
            "total_tramos": len(sel),
            "top_menor_riesgo": sel_sorted[:15],
        }
        fallback = f"Calles con menor riesgo en {alc}:\n{_fmt_list(sel_sorted, 15)}"
        return {"answer": _llm_with_facts("Redacta una respuesta breve y clara.", facts, fallback)}

    # 5) Fallback IA general (cuando no coincide ningún intent)
    if OPENROUTER_API_KEY:
        try:
            rr = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek/deepseek-chat-v3.1:free",
                    "messages": [
                        {"role": "system",
                         "content": "Eres un asistente cordial. Responde de forma breve y útil en ESPAÑOL."},
                        {"role": "user", "content": msg},
                    ],
                    "temperature": 0.5,
                },
                timeout=90,
            )
            rr.raise_for_status()
            data = rr.json()
            return {"answer": data["choices"][0]["message"]["content"].strip()}
        except Exception as e:
            return {"answer": f"⚠️ No pude usar la IA externa: {e}"}

    # Sin clave ni intent reconocido
    return {"answer": "No identifiqué una consulta de datos locales. Prueba con: «reporte 72h», "
                      "«calles con nivel alto en <alcaldía>», «promedio de lluvia en <alcaldía>»."}
