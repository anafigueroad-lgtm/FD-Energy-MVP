"""
Motor de respuestas del asistente de FD Energy.

No depende de Streamlit ni de servicios externos: sólo de la biblioteca estándar.
La interfaz (ui.py) sólo conoce el contrato:

    motor.responder(pregunta: str, contexto: dict) -> Respuesta
    motor.responder_por_id(faq_id: str, contexto: dict) -> Respuesta

Para incorporar IA en el futuro basta con crear otra clase con los mismos métodos (por ejemplo,
un motor que envíe la pregunta y la base de conocimiento a un modelo de lenguaje) y cambiarla en ui.py.

«contexto» admite las claves (todas opcionales):
    reglas            lista de dicts con regla_id, descripcion, umbral_atencion, umbral_alta,
                      ventana_dias, minimo_kwh, activa, estado, alertas_historicas
    cobertura_min_ref fracción mínima de días completos para la «Ref. promedio diario»
    hojas_base        lista de hojas requeridas del Excel
    contacto          dict con correo y whatsapp
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from . import config

RUTA_BASE = Path(__file__).with_name("base_conocimiento.json")

STOPWORDS = set("""
a al algo como con cual cuales cuando de del donde el ella en es esa ese eso esta este esto estos
fue ha hay la las le les lo los me mi mis muy no o para pero por porque puedo puede que quiero se
si sin sobre su sus te tu tus un una uno unos y ya yo hacer hago saber dime explica explicame
favor podrias ayuda ayudame significa significado quiere decir ver veo
""".split())

NOMBRES_PRIORIDAD = {"alta": "Alta", "atencion": "Atención", "informativa": "Informativa"}


@dataclass
class Respuesta:
    tipo: str                    # "respuesta" | "sugerencia" | "sin_respuesta" | "cortesia"
    texto: str
    faq_id: str | None = None
    seccion: str | None = None
    sugerencias: list = field(default_factory=list)   # [(faq_id, pregunta)]
    ofrecer_contacto: bool = False
    puntaje: float = 0.0


# =============================================================================
# Normalización
# =============================================================================
def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9ñ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _raiz(p: str) -> str:
    """Raíz ligera para igualar singular/plural («alertas» → «alerta»)."""
    if len(p) > 5 and p.endswith("es"):
        return p[:-2]
    if len(p) > 4 and p.endswith("s"):
        return p[:-1]
    return p


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _pct(v, dec=0):
    f = _num(v)
    return "—" if f is None else f"{f * 100:.{dec}f} %"


def _kwh(v):
    f = _num(v)
    return "—" if f is None else f"{f:,.0f} kWh"


# =============================================================================
# Motor basado en preguntas frecuentes
# =============================================================================
class MotorFAQ:
    def __init__(self, ruta: Path = RUTA_BASE):
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        self.faq = {f["id"]: f for f in datos["faq"]}
        self.cortesia = datos.get("cortesia", {})
        self.sinonimos = {normalizar(k): normalizar(v) for k, v in datos.get("sinonimos", {}).items()}
        self._indice = []
        # Peso de cada palabra (IDF): las que aparecen en muchas preguntas («consumo», «como») pesan menos;
        # las desconocidas pesan lo máximo, lo que reduce falsos positivos con preguntas fuera de tema.
        df = {}
        for f in datos["faq"]:
            vocab = set()
            for x in [f["pregunta"]] + f.get("variantes", []) + f.get("palabras_clave", []):
                vocab |= self._tokens(x)
            for t in vocab:
                df[t] = df.get(t, 0) + 1
        n = len(datos["faq"])
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._idf_max = math.log(1 + n)
        for f in datos["faq"]:
            frases = [f["pregunta"]] + f.get("variantes", [])
            self._indice.append({
                "id": f["id"],
                "frases": [(self._texto(x), self._tokens(x)) for x in frases],
                "claves": [self._tokens(k) for k in f.get("palabras_clave", [])],
            })

    # ------------------------------------------------------------------ texto
    def _tokens(self, texto: str) -> set[str]:
        out = set()
        for p in normalizar(texto).split():
            p = self.sinonimos.get(p, p)
            for q in p.split():
                if q not in STOPWORDS:
                    out.add(_raiz(q))
        return out

    def _texto(self, texto: str) -> str:
        return " ".join(sorted(self._tokens(texto)))

    # ------------------------------------------------------------------ puntaje
    def _puntaje(self, entrada, tq: set[str], txt: str) -> float:
        if not tq:
            return 0.0
        mejor = 0.0
        for ftxt, ft in entrada["frases"]:
            if not ft:
                continue
            w = lambda ts: sum(self._idf.get(t, self._idf_max) for t in ts)
            dice = 2 * w(tq & ft) / (w(tq) + w(ft))
            ratio = SequenceMatcher(None, txt, ftxt).ratio()
            mejor = max(mejor, 0.7 * dice + 0.3 * ratio)
        # palabras clave (una clave puede tener varias palabras: todas deben aparecer)
        aciertos = sum(1 for k in entrada["claves"] if k and k <= tq)
        frac_clave = min(1.0, aciertos / min(2, len(tq)))
        corta = 0.58 if aciertos and len(tq) <= 2 and frac_clave >= 1 else 0.0
        comunes = max([len(tq & ft) for _, ft in entrada["frases"]] + [len(k & tq) for k in entrada["claves"]])
        if comunes <= 1 and any(t not in self._idf for t in tq):
            # una sola palabra en común y otra desconocida para la base: como mucho, sugerir
            return min(config.UMBRAL_RESPUESTA - 0.01, max(mejor, 0.45 * frac_clave + 0.55 * mejor))
        return max(mejor, 0.45 * frac_clave + 0.55 * mejor, 0.6 * frac_clave if aciertos >= 2 else 0.0, corta)

    def clasificar(self, pregunta: str) -> list[tuple[str, float]]:
        tq = self._tokens(pregunta)
        txt = " ".join(sorted(tq))
        res = [(e["id"], self._puntaje(e, tq, txt)) for e in self._indice]
        return sorted(res, key=lambda x: -x[1])

    # ------------------------------------------------------------------ API
    def preguntas(self) -> list[dict]:
        return list(self.faq.values())

    def pregunta_de(self, faq_id: str) -> str:
        m = re.fullmatch(r"regla_(R\d+)", faq_id or "")
        if m:
            return f"¿Qué es la regla {m.group(1)}?"
        return self.faq[faq_id]["pregunta"] if faq_id in self.faq else faq_id

    def responder_por_id(self, faq_id: str, contexto: dict | None = None, puntaje: float = 1.0) -> Respuesta:
        contexto = contexto or {}
        m = re.fullmatch(r"regla_(R\d+)", faq_id or "")
        if m:
            return self._respuesta_regla(m.group(1), contexto)
        f = self.faq.get(faq_id)
        if f is None:
            return self._sin_respuesta(contexto)
        return Respuesta(
            tipo="respuesta", texto=self._rellenar(f["respuesta"], contexto), faq_id=faq_id,
            seccion=f.get("seccion"),
            sugerencias=[(r, self.pregunta_de(r)) for r in f.get("relacionadas", [])][:config.MAX_SUGERENCIAS],
            ofrecer_contacto=bool(f.get("ofrecer_contacto")), puntaje=puntaje)

    def responder(self, pregunta: str, contexto: dict | None = None) -> Respuesta:
        contexto = contexto or {}
        n = normalizar(pregunta)
        if not n:
            return self._sin_respuesta(contexto)

        # 1) Cortesía: se retira un saludo inicial; si no queda nada, se responde el saludo
        saludos = sorted((normalizar(x) for x in self.cortesia.get("saludos", [])), key=len, reverse=True)
        for sal in saludos:
            if n == sal or n.startswith(sal + " "):
                n = n[len(sal):].strip()
                pregunta = n
                break
        else:
            sal = None
        if not n:
            return Respuesta("cortesia", self.cortesia.get("respuesta_saludo", ""))
        gracias = [normalizar(x) for x in self.cortesia.get("agradecimientos", [])]
        if n in gracias or (len(n.split()) <= 3 and n.split()[-1] == "gracias"):
            return Respuesta("cortesia", self.cortesia.get("respuesta_agradecimiento", ""))

        # 2) Regla específica: «r4», «regla 4», «qué es la R 6» (si menciona varias, respuesta general)
        nums = set(re.findall(r"\br\s?([1-9])\b", n)) | set(re.findall(r"\breglas?\s+([1-9])\b", n))
        if len(nums) == 1:
            return self._respuesta_regla(f"R{nums.pop()}", contexto)

        # 3) Preguntas frecuentes
        ranking = self.clasificar(pregunta)
        mejor_id, mejor = ranking[0]
        if mejor >= config.UMBRAL_RESPUESTA:
            return self.responder_por_id(mejor_id, contexto, puntaje=mejor)
        candidatos = [(i, p) for i, p in ranking if p >= config.UMBRAL_SUGERENCIA][:config.MAX_SUGERENCIAS]
        if candidatos:
            return Respuesta(
                tipo="sugerencia",
                texto="No estoy seguro de haber entendido. ¿Te refieres a alguna de estas preguntas?",
                sugerencias=[(i, self.pregunta_de(i)) for i, _ in candidatos],
                ofrecer_contacto=True, puntaje=mejor)
        return self._sin_respuesta(contexto, puntaje=mejor)

    # ------------------------------------------------------------------ respuestas especiales
    def _sin_respuesta(self, contexto, puntaje=0.0) -> Respuesta:
        return Respuesta(
            tipo="sin_respuesta",
            texto=("No tengo una respuesta confiable para esa pregunta y prefiero no darte información incorrecta. "
                   "Puedes reformularla o contactar al equipo de FD Energy para recibir atención personalizada."),
            sugerencias=[("contacto", self.pregunta_de("contacto"))] if "contacto" in self.faq else [],
            ofrecer_contacto=True, puntaje=puntaje)

    def _rellenar(self, texto: str, contexto: dict) -> str:
        def rep(m):
            clave = m.group(1)
            if clave == "reglas":
                return self._tabla_reglas(contexto)
            if clave.startswith("regla:"):
                return self._detalle_regla(clave.split(":", 1)[1], contexto)
            if clave == "contacto":
                return self._texto_contacto(contexto)
            if clave == "cobertura_ref":
                c = _num(contexto.get("cobertura_min_ref"))
                return f"el {c * 100:.0f} %" if c is not None else "una proporción mínima"
            if clave == "hojas_base":
                hojas = contexto.get("hojas_base") or []
                return ", ".join(f"`{h}`" for h in hojas) if hojas else "las mismas de la base de demostración"
            return m.group(0)
        return re.sub(r"\{\{([^}]+)\}\}", rep, texto)

    @staticmethod
    def _texto_contacto(contexto: dict) -> str:
        c = contexto.get("contacto") or config.CONTACTO
        lineas = []
        if c.get("correo"):
            lineas.append(f"- **Correo:** {c['correo']}")
        if c.get("whatsapp"):
            lineas.append(f"- **WhatsApp:** +{c['whatsapp']}")
        if not lineas:
            return "Los datos de contacto aún no están configurados en esta versión del dashboard."
        return "\n".join(lineas) + "\n\nTambién puedes usar los botones de contacto que aparecen debajo de este mensaje."

    # ------------------------------------------------------------------ reglas (desde la base cargada)
    def _regla(self, rid, contexto):
        for r in contexto.get("reglas") or []:
            if str(r.get("regla_id")).upper() == rid:
                return r
        return None

    def _detalle_regla(self, rid: str, contexto: dict) -> str:
        """Explicación de cada regla según su implementación en pipeline.py, con los umbrales de la hoja
        regla_alerta de la base cargada. Si la regla no existe en la base, se dice explícitamente."""
        r = self._regla(rid, contexto)
        if r is None:
            if contexto.get("reglas") is None:
                return (f"No pude leer la configuración de la regla {rid} porque no hay una base de datos cargada. "
                        "Los umbrales se definen en la hoja `regla_alerta` del Excel.")
            return f"La regla {rid} no está definida en la hoja `regla_alerta` de la base cargada."
        ua, uh, v, mn = r.get("umbral_atencion"), r.get("umbral_alta"), _num(r.get("ventana_dias")), r.get("minimo_kwh")
        dias = f"{v:.0f} días" if v else "N días"
        comp = contexto.get("completitud_minima_reglas")
        comp_txt = f" (al menos {_pct(comp)} de intervalos válidos cada día)" if _num(comp) is not None else ""
        tpl = {
            "R1": (f"Compara el consumo de dos bloques consecutivos de {dias} (planta, áreas y equipos con medición). "
                   f"Se activa si el consumo **aumenta {_pct(ua)} o más** respecto al bloque anterior. La prioridad es **Alta** "
                   f"si el aumento llega a {_pct(uh)} y **Atención** en otro caso. Sólo se evalúa si el bloque anterior "
                   f"tiene al menos {_kwh(mn)} y ambos bloques tienen datos suficientes{comp_txt}."),
            "R2": (f"Compara el consumo de dos bloques consecutivos de {dias}. Se activa si el consumo **disminuye "
                   f"{_pct(ua)} o más**; la prioridad es **Informativa**, para confirmar si corresponde a un cambio operativo. "
                   f"Requiere que el bloque anterior tenga al menos {_kwh(mn)} y datos suficientes{comp_txt}."),
            "R3": (f"Compara la generación fotovoltaica de dos bloques consecutivos de {dias}. Se activa si la generación "
                   f"**disminuye {_pct(ua)} o más**; prioridad **Atención**. Requiere análisis porque puede deberse a "
                   f"condiciones climáticas."),
            "R4": (f"Compara la generación fotovoltaica de cada día con el **promedio de los 7 días previos**. Se activa si "
                   f"la generación del día es **menor al {_pct(uh)} de ese promedio**; prioridad **Alta**, para revisar "
                   f"el inversor y la comunicación."),
            "R5": (f"Revisa la completitud diaria de cada medidor. Se activa si un medidor tiene **menos de {_pct(ua)} de "
                   f"datos válidos** en un día (incluidos días sin lecturas); prioridad **Atención**, porque los "
                   f"indicadores de ese día pueden estar incompletos."),
            "R6": (f"Verifica el balance de medición. Se activa si la suma de los submedidores de área **supera el consumo "
                   f"de planta en más de {_pct(ua)}** (o la suma de equipos supera a su medidor padre); prioridad "
                   f"**Atención**, para revisar la configuración o calibración. A nivel planta sólo se evalúa en días con "
                   f"balance y todas las áreas completas."),
            "R7": (f"No es una alerta sino una **oportunidad**. Se identifica cuando un área representa al menos "
                   f"{_pct(ua)} del consumo de planta (o un equipo, al menos {_pct(ua)} del consumo de su área) y además "
                   f"**aumentó {_pct(self._umbral_r1(contexto))} o más** contra el periodo anterior. Sólo se usa la variación del "
                   f"total (no la referencia de promedio diario) y el periodo anterior debe tener al menos {_kwh(mn)}."),
        }.get(rid, "Su comportamiento no está documentado en el asistente; consulta la pestaña *Reglas evaluadas*.")
        desc = str(r.get("descripcion") or "").strip()
        extra = []
        if desc and desc.lower() != "nan":
            extra.append(f"*Descripción en la base:* {desc}")
        est = r.get("estado")
        if est:
            n = r.get("alertas_historicas")
            extra.append(f"*Estado:* {est}" + (f" · {int(n)} alerta(s) en todo el histórico" if _num(n) is not None and rid != "R7" else ""))
        return tpl + ("\n\n" + "  \n".join(extra) if extra else "")

    @staticmethod
    def _umbral_r1(contexto):
        for r in contexto.get("reglas") or []:
            if str(r.get("regla_id")).upper() == "R1":
                return r.get("umbral_atencion")
        return None

    def _tabla_reglas(self, contexto: dict) -> str:
        reglas = contexto.get("reglas")
        if not reglas:
            return "_No hay una base de datos cargada para leer las reglas configuradas._"
        partes = []
        for r in sorted(reglas, key=lambda x: str(x.get("regla_id"))):
            rid = str(r.get("regla_id")).upper()
            if rid == "R7":
                continue
            if _num(r.get("activa")) is not None and _num(r.get("activa")) != 1:
                partes.append(f"- **{rid}** — inactiva en la base cargada.")
                continue
            partes.append(f"- **{rid}.** " + self._detalle_regla(rid, contexto).split("\n\n")[0])
        partes.append("\n**R7** no es una alerta: identifica *oportunidades* (pregúntame «¿qué es la R7?»).")
        return "\n".join(partes)

    def _respuesta_regla(self, rid: str, contexto: dict) -> Respuesta:
        seccion = "Alertas y oportunidades"
        if self._regla(rid, contexto) is None and contexto.get("reglas") is not None:
            return Respuesta("respuesta", self._detalle_regla(rid, contexto), faq_id=f"regla_{rid}", seccion=seccion,
                             sugerencias=[("alertas_reglas", self.pregunta_de("alertas_reglas"))], puntaje=1.0)
        relacionadas = {"R1": ["regla_R2", "equipos_criticos"], "R2": ["regla_R1", "alertas_reglas"],
                        "R3": ["regla_R4", "energia_solar"], "R4": ["regla_R3", "energia_solar"],
                        "R5": ["datos_incompletos", "regla_R6"], "R6": ["consumo_areas", "regla_R5"],
                        "R7": ["oportunidades", "regla_R1"]}.get(rid, ["alertas_reglas"])
        titulo = "Oportunidad" if rid == "R7" else "Regla"
        return Respuesta("respuesta", f"**{titulo} {rid}.** " + self._detalle_regla(rid, contexto),
                         faq_id=f"regla_{rid}", seccion=seccion,
                         sugerencias=[(i, self.pregunta_de(i)) for i in relacionadas], puntaje=1.0)
