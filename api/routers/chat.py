# api/routers/chat.py
import os
import re
import json
import requests
import unicodedata
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/chat", tags=["chat"])

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OR_SITE = os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000")
OR_APP  = os.getenv("OPENROUTER_APP_NAME", "CDMX Flood")
OR_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3.1:free")

# ==================== Modelos ====================
class ChatReq(BaseModel):
    question: Optional[str] = None
    q: Optional[str] = None  # compat

# ==================== Utils ====================
def _http_get(path: str, params: Dict[str, Any]) -> Any:
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def _strip_accents(s: str) -> str:
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _norm(s: str) -> str:
    s2 = _strip_accents((s or "").lower().strip())
    s2 = re.sub(r"\s+", " ", s2)
    return s2

def _clean_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).title()

def _dedup(rows: List[Dict[str, Any]], key=("nombre", "alcaldia")) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        k = (_norm(r.get(key[0]) or r.get("calle") or ""), _norm(r.get(key[1]) or ""))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out

def _fmt_list(rows: List[Dict[str, Any]], maxn=10) -> str:
    rows = _dedup(rows)
    out = []
    for r in rows[:maxn]:
        calle = r.get("calle") or r.get("nombre") or "(sin nombre)"
        alc   = r.get("alcaldia") or "-"
        p72   = float(r.get("p72_mm") or 0.0)
        niv   = r.get("nivel") or "-"
        out.append(f"• {calle} — {alc} | p72={p72:.1f} mm | {niv}")
    return "\n".join(out) if out else "(sin resultados)"

def _alcaldia_alias(s: str) -> str:
    n = _norm(s.strip(" .:;,'\"!?"))
    alias = {
        # abreviaturas
        "gam": "Gustavo A. Madero",
        "bj": "Benito Juárez",
        "vc": "Venustiano Carranza",
        "mh": "Miguel Hidalgo",
        # variantes y typos comunes
        "gustavo a. madero": "Gustavo A. Madero",
        "gustavo a madero": "Gustavo A. Madero",
        "iztapalapa": "Iztapalapa", "iztapa": "Iztapalapa",
        "alvaro obregon": "Álvaro Obregón", "alvaro-obregon": "Álvaro Obregón",
        "benito-juarez": "Benito Juárez", "benito juarez": "Benito Juárez",
        "magdalena contreras": "La Magdalena Contreras", "contreras": "La Magdalena Contreras",
        "venustiano": "Venustiano Carranza",
        "miguel-hidalgo": "Miguel Hidalgo",
        "cuahutemoc": "Cuauhtémoc", "cuauhtemoc": "Cuauhtémoc",
        "tlahuac": "Tláhuac", "xochimilco": "Xochimilco",
        "azcapotzalco": "Azcapotzalco", "coyoacan": "Coyoacán",
        "tlalpan": "Tlalpan", "milpa alta": "Milpa Alta",
        "cuajimalpa": "Cuajimalpa de Morelos", "cuajimalpa de morelos": "Cuajimalpa de Morelos",
        "la magdalena contreras": "La Magdalena Contreras",
    }
    return alias.get(n, _clean_name(s))

def _best_match_streets(term: str, rows: List[Dict[str, Any]], alcaldia: Optional[str]=None, maxn: int=10):
    t = _norm(term)
    if not t:
        return []
    cand = []
    for r in rows:
        nombre = _norm(r.get("nombre") or r.get("calle") or "")
        alc    = _norm(r.get("alcaldia") or "")
        if t in nombre and (alcaldia is None or _norm(alcaldia) in alc):
            cand.append(r)
    if not cand and len(t) >= 4:
        toks = [w for w in t.split() if len(w) >= 4]
        for r in rows:
            nombre = _norm(r.get("nombre") or r.get("calle") or "")
            alc    = _norm(r.get("alcaldia") or "")
            if all(w in nombre for w in toks) and (alcaldia is None or _norm(alcaldia) in alc):
                cand.append(r)
    cand = _dedup(cand)
    cand.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return cand[:maxn]

# ==================== IA helpers ====================
def _sanitize_ai(text: str, max_chars: int = 1400) -> str:
    """Quita repeticiones raras y recorta textos demasiado largos."""
    if not text:
        return text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dedup = []
    last = None; rep = 0
    for ln in lines:
        if ln == last:
            rep += 1
            if rep > 2:
                continue
        else:
            last = ln; rep = 0
        dedup.append(ln)
    text2 = "\n".join(dedup)
    if len(text2) > max_chars:
        text2 = text2[:max_chars].rstrip() + "…"
    return text2

def _llm_with_facts(task: str, facts: Dict[str, Any], fallback_text: str) -> str:
    """Parafrasea SOLO con facts. Si falla la IA → fallback_text."""
    if not OPENROUTER_API_KEY:
        return fallback_text
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OR_SITE,
        "X-Title": OR_APP,
    }
    system = (
        "Eres un asistente que redacta respuestas claras y naturales EN ESPAÑOL, "
        "usando EXCLUSIVAMENTE los datos de 'FACTS'. "
        "NO inventes ni agregues datos ausentes. "
        "NO agregues fechas, lugares o nombres que no aparezcan literalmente en FACTS. "
        "Si algún dato no está, di 'no disponible'. Sé breve y directo."
    )
    user = (
        f"TAREA: {task}\n\nFACTS (JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Estilo: 1–3 párrafos cortos o viñetas; usa mm cuando aplique; sin fuentes genéricas."
    )
    try:
        rr = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": OR_MODEL,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0.3,
            },
            timeout=90,
        )
        rr.raise_for_status()
        data = rr.json()
        out = data["choices"][0]["message"]["content"].strip()
        return _sanitize_ai(out)
    except Exception:
        return fallback_text

def _llm_general(msg: str) -> str:
    """IA general para preguntas fuera del backend, con fallback determinista útil."""
    # 1) Intento con IA externa
    if OPENROUTER_API_KEY:
        try:
            rr = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": OR_SITE,
                    "X-Title": OR_APP,
                },
                json={
                    "model": OR_MODEL,
                    "messages": [
                        {"role": "system", "content": "Responde breve y útil en ESPAÑOL."},
                        {"role": "user", "content": msg},
                    ],
                    "temperature": 0.4,
                },
                timeout=90,
            )
            rr.raise_for_status()
            data = rr.json()
            out = data["choices"][0]["message"]["content"].strip()
            return _sanitize_ai(out)
        except Exception:
            pass

    # 2) Fallback determinista (sin IA)
    g = _norm(msg)
    if any(w in g for w in ["kit", "inunda", "inundac", "lluvia fuerte", "tormenta", "moho"]):
        return ("Kit básico contra inundaciones:\n"
                "• Linterna, pilas, cargador portátil\n"
                "• Botiquín y medicinas\n"
                "• Agua y enlatados para 72h\n"
                "• Impermeable, botas, documentos en bolsa hermética\n"
                "• Eleva aparatos y corta la luz si hay agua cerca")
    if any(w in g for w in ["emerg", "telefono", "teléfono", "locatel", "proteccion civil", "protección civil"]):
        return ("Teléfonos útiles en CDMX:\n"
                "• 911 (emergencias)\n"
                "• Locatel: 55 5658 1111\n"
                "• Protección Civil CDMX: 800 111 4636")
    if any(w in g for w in ["animal", "extins", "extinci", "extinción"]):
        return ("Animales en peligro (ejemplos):\n"
                "• Ajolote (México)\n• Jaguar\n• Vaquita marina\n• Panda gigante\n• Orangután")
    if any(w in g for w in ["xbox", "playstation", "ps5", "nintendo", "juego", "videojuego"]):
        return ("Dime el género que te gusta (acción, RPG, deportes, carreras, cooperativo) y te sugiero 3–5 juegos.")
    # genérico
    return "No pude usar la IA externa ahora, pero puedo darte guías, teléfonos o recomendaciones básicas si me das un poco más de contexto."

# ==================== Regex de intents ====================
_re_resumen = re.compile(r"(reporte|resumen|sumario|pron[oó]stico|proxim[ao]s?\s*72\s*h?r?s?)", re.I)
_re_topic = re.compile(r"(lluvia|llover|llueva|p72|inundaci[oó]n|riesgo|probabilidad|probable)", re.I)

_re_calles_nivel_en = re.compile(
    r"(calles?|cu[aá]les\s+son|dime|lista|mu[eé]strame).{0,80}nivel\s+(alto|medio|bajo).{0,80}(en|de)\s+([a-záéíóúñ\.\- ]+)$",
    re.I
)

_re_lluvia_en_alc = re.compile(
    r"(promedio|media|lluvia|llover[aá]?|llueva|p72|mm|que\s+tanto\s+va\s+a\s+llover|va\s+a\s+llover).{0,80}(en|de)\s+([a-záéíóúñ\.\- ]+)$",
    re.I
)

_re_menos_riesgo = re.compile(
    r"(menos\s+probables|menor\s+riesgo|riesgo\s+bajo|calles\s+con\s+(menor|bajo)\s+riesgo).{0,80}(en|de)\s+([a-záéíóúñ\.\- ]+)$",
    re.I
)

# Calle (robustecido: comillas/paréntesis y tipos de vía)
_re_prob_en_calle = re.compile(
    r"(?:probabilidad|lluvia|llover|llueva|inundaci[oó]n|riesgo).*?(?:en|sobre)\s+(?:la\s+|el\s+)?"
    r"(?:calle|av(?:\.|enida)?|calz(?:\.|ada)?|viaducto|paseo|blvd\.?|boulevard)?\s*"
    r"(?P<street>[a-z0-9 áéíóúñ\.\-\"'()]+?)(?:\s+(?:en|de)\s+(?P<alc>[a-z áéíóúñ\.\-]+))?$",
    re.I
)

_re_riesgo_en_alc = re.compile(
    r"(riesgo|probabilidad|inundaci[oó]n|lluvia).{0,80}(en|de)\s+([a-záéíóúñ\.\- ]+)$",
    re.I
)

_re_top_alc = re.compile(
    r"(?:^|\b)(?:top\s*\d*\s*alcald[ií]as?|alcald[ií]as?.*?(?:m[aá]s|mayor).*(?:lluvia|riesgo|inundaci[oó]n)|"
    r"top\s*(?:lluvia|riesgo|inundaci[oó]n)\s*alcald[ií]as?)\b",
    re.I
)

# ==================== Router principal ====================
@router.post("")
def chat(req: ChatReq):
    raw = (req.question or req.q or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="Falta 'question' en el JSON.")
    msg_nop = re.sub(r"[()]", " ", raw).replace("’", "'").strip()
    low = _norm(msg_nop)
    is_topic = bool(_re_topic.search(msg_nop))

    # 1) Resumen 72h
    if _re_resumen.search(msg_nop):
        try:
            summ = _http_get("forecast/summary", {"from_hours": 0, "to_hours": 72})
        except Exception as e:
            return {"answer": f"⚠️ No pude leer el resumen 72h: {e}"}
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 800, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception:
            filas = []
        facts = {
            "ventana_utc": summ.get("window_utc", {}),
            "n_celdas": int(summ.get("n_cells", 0)),
            "lluvia_total_mm": float(summ.get("mm_sum", 0.0)),
            "top_calles": filas[:10],
        }
        fallback = (f"Resumen 72h: lluvia total {facts['lluvia_total_mm']:.1f} mm, "
                    f"celdas={facts['n_celdas']}. Top calles:\n{_fmt_list(facts['top_calles'], 10)}")
        return {"answer": _llm_with_facts("Redacta un resumen claro (72h).", facts, fallback)}

    # 2) Calles con nivel (alto|medio|bajo) en <alcaldía>
    m = _re_calles_nivel_en.search(msg_nop)
    if m:
        nivel = (m.group(2) or "").lower()
        alc   = _alcaldia_alias(m.group(4) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 5000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}

        sel = [r for r in filas
               if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0
               and _norm(r.get("nivel") or "") == _norm(nivel)]

        if not sel:
            cand = [r for r in filas if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0]
            cand.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
            cand = cand[:15]
            if cand:
                txt = (f"No encontré tramos con nivel {nivel} en {alc}.\n"
                       f"Estas son las calles con mayor score ahora mismo en {alc}:\n{_fmt_list(cand, 15)}")
                return {"answer": txt}
            else:
                return {"answer": f"No tengo tramos para {alc} en este momento."}

        facts = {"alcaldia": alc, "nivel": nivel, "total_tramos": len(sel), "ejemplos": sel[:15]}
        fallback = f"Calles con nivel {nivel} en {alc}:\n{_fmt_list(sel, 15)}"
        return {"answer": _llm_with_facts("Redacta breve con ejemplos de calles.", facts, fallback)}

    # 3) Promedio/lluvia/p72 en <alcaldía>
    m2 = _re_lluvia_en_alc.search(msg_nop)
    if m2:
        alc = _alcaldia_alias(m2.group(3) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 5000, "tolerance_m": 5,
                "use_hazard": False, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}
        sel = [r for r in filas if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0]
        if not sel:
            # fallback: intenta con hazard por si estaba vacío
            try:
                sc2 = _http_get("score", {
                    "hours": 72, "top_k": 5000, "tolerance_m": 5,
                    "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
                })
                filas2 = sc2.get("rows", [])
            except Exception:
                filas2 = []
            sel = [r for r in filas2 if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0]
            if not sel:
                return {"answer": f"No tengo datos recientes para {alc}. Intenta con otra alcaldía o revisa más tarde."}
        vals = [float(r.get("p72_mm") or 0.0) for r in sel]
        avg = sum(vals)/len(vals); mx = max(vals); mn = min(vals)
        facts = {"alcaldia": alc, "n_tramos": len(sel),
                 "p72_prom_mm": round(avg, 1), "p72_max_mm": round(mx, 1), "p72_min_mm": round(mn, 1)}
        fallback = (f"Lluvia 72h en {alc} (sobre {facts['n_tramos']} tramos): "
                    f"prom={facts['p72_prom_mm']} mm, máx={facts['p72_max_mm']} mm, mín={facts['p72_min_mm']} mm.")
        return {"answer": _llm_with_facts("Resume la lluvia prevista (72h).", facts, fallback)}

    # 4) Menor riesgo en <alcaldía>
    m3 = _re_menos_riesgo.search(msg_nop)
    if m3:
        alc = _alcaldia_alias(m3.group(3) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 5000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", [])
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}
        sel = [r for r in filas if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0]
        if not sel:
            return {"answer": f"No tengo tramos cargados para {alc} en este momento."}
        sel_sorted = sorted(sel, key=lambda r: float(r.get("score") or 0.0))
        facts = {"alcaldia": alc, "total_tramos": len(sel), "top_menor_riesgo": sel_sorted[:15]}
        fallback = f"Calles con menor riesgo en {alc}:\n{_fmt_list(sel_sorted, 15)}"
        return {"answer": _llm_with_facts("Redacta breve con ejemplos.", facts, fallback)}

    # 5) Probabilidad/lluvia/inundación en <calle> (opcional <alcaldía>)
    m4 = _re_prob_en_calle.search(msg_nop)
    if m4 or (is_topic and ("calzad" in low or "avenida" in low or "calle" in low or "viaducto" in low or "paseo" in low)):
        if m4:
            raw_calle = (m4.group("street") or "").strip()
            calle_q = _clean_name(re.sub(r'["\']', "", raw_calle))
            alc_q = _alcaldia_alias(m4.group("alc") or "") if m4.group("alc") else None
        else:
            m_guess = re.search(r"(?:en|sobre)\s+([a-z0-9 áéíóúñ\.\-\"']+)", msg_nop, re.I)
            calle_q = _clean_name(re.sub(r'["\']', "", m_guess.group(1))) if m_guess else ""
            alc_q = None

        # sanea si quedó vacío
        if not calle_q or calle_q.lower() in ["en", "de", "la", "el"]:
            # intenta extraer entre comillas
            m_q = re.search(r'["“](.+?)["”]', msg_nop)
            if m_q:
                calle_q = _clean_name(m_q.group(1))
        if not calle_q:
            # toma última frase después del último "en"/"sobre"
            parts = re.split(r"\b(?:en|sobre)\b", msg_nop, flags=re.I)
            if len(parts) >= 2:
                calle_q = _clean_name(parts[-1])

        if not calle_q:
            return {"answer": "No pude identificar el nombre de la calle. Intenta: «Riesgo en Calzada Ignacio Zaragoza (en Iztapalapa)»."}

        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 10000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", []) or []
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}

        matches = _best_match_streets(calle_q, filas, alcaldia=alc_q, maxn=8)

        if not matches and alc_q:
            matches_any = _best_match_streets(calle_q, filas, alcaldia=None, maxn=5)
            if matches_any:
                sugerencias = _fmt_list(matches_any, 5)
                return {"answer": (f"No encontré tramos de «{calle_q}» en {alc_q}. "
                                   f"Coincidencias en otras alcaldías:\n{sugerencias}")}
            return {"answer": f"No encontré tramos de «{calle_q}» en {alc_q}. Prueba con un cruce o colonia cercana."}

        if not matches:
            return {"answer": f"No encontré tramos que contengan «{calle_q}». Prueba con un nombre más corto o un cruce."}

        best = matches[0]
        facts = {
            "consulta_calle": calle_q,
            "consulta_alcaldia": alc_q or "-",
            "mejor_match": {
                "nombre": best.get("nombre") or best.get("calle") or "(sin nombre)",
                "alcaldia": best.get("alcaldia") or "-",
                "p72_mm": float(best.get("p72_mm") or 0.0),
                "nivel": best.get("nivel") or "-",
                "score": float(best.get("score") or 0.0),
            },
            "otros_tramos": _dedup(matches[1:5]),
        }
        fallback = (
            f"{facts['mejor_match']['nombre']} — {facts['mejor_match']['alcaldia']} | "
            f"p72={facts['mejor_match']['p72_mm']:.1f} mm | {facts['mejor_match']['nivel']} "
            f"(score={facts['mejor_match']['score']:.2f})\n"
            + ("Otros:\n" + _fmt_list(matches[1:5], 4) if len(matches) > 1 else "")
        )
        return {"answer": _llm_with_facts("Explica brevemente la probabilidad para esa calle.", facts, fallback)}

    # 6) Riesgo/lluvia genérico en <alcaldía> (sin “nivel”/“promedio”)
    m5 = _re_riesgo_en_alc.search(msg_nop)
    if m5:
        alc = _alcaldia_alias(m5.group(3) or "")
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 8000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", []) or []
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}

        if not filas:
            try:
                sc = _http_get("score", {
                    "hours": 72, "top_k": 8000, "tolerance_m": 5,
                    "use_hazard": False, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
                })
                filas = sc.get("rows", []) or []
            except Exception:
                filas = []

        sel = [r for r in filas if _norm(r.get("alcaldia") or "").find(_norm(alc)) >= 0]
        if not sel:
            return {"answer": f"No encontré tramos para {alc} en los datos actuales. Intenta con otra alcaldía o más tarde."}

        n = len(sel)
        n_alto = sum(1 for r in sel if _norm(r.get("nivel") or "") == "alto")
        prom_score = sum(float(r.get("score") or 0.0) for r in sel)/n
        prom_mm = sum(float(r.get("p72_mm") or 0.0) for r in sel)/n
        facts = {
            "alcaldia": alc, "tramos": n, "tramos_alto": n_alto,
            "score_prom": round(prom_score, 2), "p72_prom_mm": round(prom_mm, 1),
            "ejemplos": sel[:12],
        }
        fallback = (f"Riesgo en {alc} (72h): alto={n_alto}/{n} tramos, "
                    f"score_prom={facts['score_prom']:.2f}, p72_prom={facts['p72_prom_mm']:.1f} mm.\n"
                    f"Ejemplos:\n{_fmt_list(sel, 8)}")
        return {"answer": _llm_with_facts("Resume el riesgo general por alcaldía (72h).", facts, fallback)}

    # 7) Top alcaldías (lluvia/riesgo/inundación)
    if _re_top_alc.search(msg_nop):
        try:
            sc = _http_get("score", {
                "hours": 72, "top_k": 12000, "tolerance_m": 5,
                "use_hazard": True, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
            })
            filas = sc.get("rows", []) or []
        except Exception as e:
            return {"answer": f"⚠️ No pude leer score: {e}"}

        if not filas:
            try:
                sc = _http_get("score", {
                    "hours": 72, "top_k": 12000, "tolerance_m": 5,
                    "use_hazard": False, "min_mm": 0.0, "only_cdmx": True, "mm_ref": 80
                })
                filas = sc.get("rows", []) or []
            except Exception:
                filas = []

        if not filas:
            return {"answer": "No hay filas disponibles en /score para calcular el top de alcaldías en este momento."}

        agg: Dict[str, Dict[str, Any]] = {}
        for r in filas:
            alc = (r.get("alcaldia") or "-").strip()
            s   = float(r.get("score") or 0.0)
            mm  = float(r.get("p72_mm") or 0.0)
            lvl = _norm(r.get("nivel") or "")
            a = agg.setdefault(alc, {"n": 0, "n_alto": 0, "sum_score": 0.0, "sum_mm": 0.0, "max_score": 0.0})
            a["n"] += 1
            if lvl == "alto":
                a["n_alto"] += 1
            a["sum_score"] += s
            a["sum_mm"]    += mm
            a["max_score"]  = max(a["max_score"], s)

        items = []
        for alc, v in agg.items():
            if v["n"] == 0 or alc == "-" or v["n"] < 5:  # filtra sin nombre y pocos tramos
                continue
            prom_score = v["sum_score"]/v["n"]
            prom_mm    = v["sum_mm"]/v["n"]
            items.append((alc, v["n_alto"], prom_score, v["max_score"], prom_mm))

        if not items:
            return {"answer": "Tengo filas, pero el agregado por alcaldía quedó vacío. Intenta de nuevo en unos minutos."}

        items.sort(key=lambda x: (x[1], x[2]), reverse=True)
        top = items[:10]
        lines = [f"• {alc}: alto={n_al} | score_prom={prom:.2f} | score_max={mx:.2f} | p72_prom={mm:.1f} mm"
                 for (alc, n_al, prom, mx, mm) in top]
        facts = {"top_alcaldias": lines}
        fallback = "**Top alcaldías (72h, según backend)**\n" + "\n".join(lines)
        return {"answer": _llm_with_facts("Redacta un top breve de alcaldías (72h).", facts, fallback)}

    # 8) Si la pregunta NO es del tema (backend) → IA general (con fallback útil)
    if not is_topic:
        return {"answer": _llm_general(raw)}

    # 9) Pregunta de tema clima pero sin encajar arriba → IA general (con fallback útil)
    return {"answer": _llm_general(raw)}