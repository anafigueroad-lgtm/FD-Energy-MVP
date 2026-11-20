"""
Ventana flotante del asistente de FD Energy (Streamlit).

Uso desde app.py (una sola línea, después de mostrar la sección activa):
    from asistente.ui import asistente_flotante
    asistente_flotante(ctx, pl)

Funcionamiento:
- Un botón 💬 fijo en la esquina inferior derecha (st.popover dentro de un contenedor con posición fija).
- La conversación vive en un @st.fragment: enviar mensajes o pulsar sugerencias sólo vuelve a ejecutar el
  chat, no el dashboard. El historial se guarda en st.session_state y se conserva al cambiar de sección.
- No modifica ni recalcula indicadores: sólo lee la configuración de reglas de la base ya procesada
  (ctx["base"]["regla_alerta"] y ctx["df_estado_reglas"]) para citar umbrales reales.

Nota técnica: la posición fija usa las clases que Streamlit genera para contenedores con key
(.st-key-…) y los data-testid del popover. Se probó con Streamlit 1.67.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime

import pandas as pd
import streamlit as st

from . import config
from .motor import MotorFAQ, Respuesta

KEY_SECCION = "seccion"                  # key del st.radio de secciones en app.py
K_HIST, K_PEND = "fd_chat_historial", "fd_chat_sin_respuesta"

NAVY, INK, MUTED, GRID, BAND, SOLAR = "#1F3A5C", "#1F2E45", "#6B7480", "#E3E7EC", "#F1F4F7", "#D4A72C"
PRINCIPALES = ["indicadores", "importacion_exportacion", "consumo_areas", "equipos_criticos",
               "alertas_reglas", "oportunidades", "contacto"]
POR_SECCION = {
    "Resumen general": ["indicadores", "importacion_exportacion", "variaciones"],
    "Consumo por áreas": ["consumo_areas", "datos_incompletos", "oportunidades"],
    "Equipos críticos": ["equipos_criticos", "alertas_reglas", "oportunidades"],
    "Energía solar": ["energia_solar", "importacion_exportacion", "regla_R4"],
    "Climatización": ["climatizacion", "alertas_reglas", "equipos_criticos"],
    "Alertas y oportunidades": ["alertas_reglas", "oportunidades", "regla_R1"],
}


# =============================================================================
# Configuración y contexto
# =============================================================================
@st.cache_resource(show_spinner=False)
def _motor() -> MotorFAQ:
    return MotorFAQ()


def contacto() -> dict:
    """Datos de contacto: config.py, sobrescribibles con st.secrets["asistente"]."""
    c = dict(config.CONTACTO)
    try:
        sec = st.secrets.get("asistente", {})
        for k in ("correo", "whatsapp"):
            if sec.get(k):
                c[k] = str(sec[k]).strip()
    except Exception:          # sin archivo de secrets: se usa config.py
        pass
    c["whatsapp"] = re.sub(r"\D", "", c.get("whatsapp") or "")
    return c


def construir_contexto(ctx=None, pipeline=None) -> dict:
    out = {"contacto": contacto(), "reglas": None}
    if pipeline is not None:
        out["cobertura_min_ref"] = getattr(pipeline, "COBERTURA_MIN_REF", None)
        out["hojas_base"] = getattr(pipeline, "TABLAS_BASE", None)
    if ctx is not None:
        try:
            ra = ctx["base"]["regla_alerta"].copy()
            est = ctx.get("df_estado_reglas")
            if est is not None and len(est):
                ra = ra.merge(est[["regla_id", "estado", "alertas_historicas"]], on="regla_id", how="left")
            out["reglas"] = ra.astype(object).where(ra.notna(), None).to_dict("records")
            idx = ra.set_index("regla_id")
            if "R5" in idx.index:          # el pipeline usa el umbral de R5 como completitud mínima de R1–R3
                out["completitud_minima_reglas"] = idx.loc["R5", "umbral_atencion"]
        except Exception:
            out["reglas"] = None
    return out


# =============================================================================
# Estado de la conversación
# =============================================================================
def _registrar(pregunta: str, r: Respuesta):
    st.session_state[K_HIST] += [
        {"rol": "user", "texto": pregunta},
        {"rol": "assistant", "texto": r.texto, "tipo": r.tipo, "faq_id": r.faq_id, "seccion": r.seccion,
         "sugerencias": list(r.sugerencias), "contacto": r.ofrecer_contacto, "pregunta_usuario": pregunta}]
    if r.tipo in ("sin_respuesta", "sugerencia"):
        st.session_state[K_PEND].append({
            "fecha_hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "pregunta": pregunta,
            "resultado": "Sin respuesta" if r.tipo == "sin_respuesta" else "Pidió aclaración",
            "seccion": st.session_state.get(KEY_SECCION) or ""})


def _cb_preguntar_id(faq_id: str, contexto: dict):
    motor = _motor()
    _registrar(motor.pregunta_de(faq_id), motor.responder_por_id(faq_id, contexto))


def _cb_mas_preguntas(contexto: dict):
    fid = st.session_state.get("fd_chat_mas")
    if fid:
        _cb_preguntar_id(fid, contexto)
    st.session_state["fd_chat_mas"] = None


def _cb_ir_a(seccion: str):
    st.session_state[KEY_SECCION] = seccion
    st.session_state["fd_chat_navegar"] = True


def _cb_reiniciar():
    st.session_state[K_HIST] = []


# =============================================================================
# Componentes
# =============================================================================
def _url_correo(c: dict, pregunta: str = "") -> str:
    cuerpo = ("Hola, equipo de FD Energy:\n\n"
              + (f"Tengo la siguiente duda sobre el dashboard:\n«{pregunta}»\n\n" if pregunta else "")
              + "Quedo atento(a) a su respuesta.")
    q = urllib.parse.urlencode({"subject": c.get("asunto_correo", ""), "body": cuerpo}, quote_via=urllib.parse.quote)
    return f"mailto:{c['correo']}?{q}"


def _url_whatsapp(c: dict, pregunta: str = "") -> str:
    texto = "Hola, equipo de FD Energy. " + (f"Tengo una duda sobre el dashboard: «{pregunta}»" if pregunta
                                             else "Necesito apoyo con el dashboard.")
    return f"https://wa.me/{c['whatsapp']}?text={urllib.parse.quote(texto)}"


def botones_contacto(c: dict, pregunta: str = "", etiqueta_correo="✉️ Escribir a un asesor"):
    canales = [(etiqueta_correo, _url_correo(c, pregunta))] if c.get("correo") else []
    if c.get("whatsapp"):
        canales.append(("💬 WhatsApp", _url_whatsapp(c, pregunta)))
    if not canales:
        st.caption("Los datos de contacto aún no están configurados.")
        return
    cols = st.columns(len(canales))
    for col, (etq, url) in zip(cols, canales):
        col.link_button(etq, url, width="stretch")


def _css():
    m = config.BOTON_MARGEN_PX
    st.markdown(f"""
<style>
  /* Los elementos del asistente no ocupan espacio en el flujo de la página */
  [data-testid="stElementContainer"]:has(.fd-chat-css) {{display: none;}}
  [data-testid="stLayoutWrapper"]:has(> .st-key-fd_chat_fab) {{position: absolute; width: 0; height: 0;}}
  /* Botón flotante */
  .st-key-fd_chat_fab {{position: fixed; bottom: {m}px; right: {m}px; z-index: 999990; width: auto !important;}}
  .st-key-fd_chat_fab [data-testid="stPopoverButton"] {{
      width: 58px; height: 58px; min-height: 58px; padding: 0; border-radius: 50%;
      background: {NAVY} !important; color: #fff !important; border: 2px solid {NAVY} !important;
      box-shadow: 0 6px 18px rgba(31, 58, 92, .32); transition: transform .15s ease, border-color .15s ease;}}
  .st-key-fd_chat_fab [data-testid="stPopoverButton"]:hover {{transform: translateY(-2px); border-color: {SOLAR} !important;}}
  .st-key-fd_chat_fab [data-testid="stPopoverButton"] p {{font-size: 1.45rem; line-height: 1; margin: 0;}}
  .st-key-fd_chat_fab [data-testid="stPopoverButton"] [data-testid="stIconMaterial"] {{display: none;}}
  /* Ventana */
  div[data-testid="stPopoverBody"]:has(.st-key-fd_chat_ventana) {{
      width: min({config.VENTANA_ANCHO_PX}px, 92vw); max-width: 92vw; max-height: 82vh; padding: 0; overflow-y: auto !important;
      border: 1px solid {GRID}; border-radius: 14px; box-shadow: 0 12px 32px rgba(31, 46, 69, .18);}}
  .st-key-fd_chat_ventana {{gap: .45rem;}}
  .fd-chat-top {{background: {NAVY}; color: #fff; padding: 12px 16px 10px; border-top: 3px solid {SOLAR};}}
  .fd-chat-top b {{font-size: 1rem; letter-spacing: -0.01em;}}
  .fd-chat-top span {{display: block; font-size: .74rem; color: #C9D4E0; margin-top: 2px;}}
  .st-key-fd_chat_cuerpo {{padding: 0 12px 10px;}}
  .st-key-fd_chat_cuerpo [data-testid="stChatMessage"] {{padding: .35rem .2rem; gap: .55rem; background: transparent;}}
  .st-key-fd_chat_cuerpo [data-testid="stChatMessage"] p,
  .st-key-fd_chat_cuerpo [data-testid="stChatMessage"] li {{font-size: .86rem; color: {INK}; line-height: 1.45;}}
  .st-key-fd_chat_cuerpo button p {{font-size: .8rem;}}
  .st-key-fd_chat_cuerpo [data-testid="stBaseButton-secondary"] {{min-height: 1.9rem; padding: .15rem .6rem; text-align: left;}}
  .fd-chat-sub {{color: {MUTED}; font-size: .68rem; font-weight: 600; text-transform: uppercase;
                 letter-spacing: .05em; margin: .35rem 0 .15rem;}}
  .fd-chat-nota {{color: {MUTED}; font-size: .7rem; line-height: 1.3;}}
  /* Margen para que el botón no tape el final de la página */
  .block-container {{padding-bottom: 6.5rem !important;}}
</style><span class="fd-chat-css"></span>""", unsafe_allow_html=True)


def _mostrar_texto(texto: str, i: int):
    """Respuestas largas: primer bloque visible y el resto en un desplegable."""
    if len(texto) <= config.RESPUESTA_LARGA_CARACTERES or "\n\n" not in texto:
        st.markdown(texto)
        return
    primero, resto = texto.split("\n\n", 1)
    st.markdown(primero)
    with st.expander("Ver respuesta completa"):
        st.markdown(resto)


def _acciones(m: dict, i: int, contexto: dict, c: dict):
    if m.get("seccion") and m["seccion"] != st.session_state.get(KEY_SECCION):
        st.button(f"Ir a {m['seccion']} →", key=f"fd_ir_{i}", on_click=_cb_ir_a, args=(m["seccion"],))
    if m.get("sugerencias"):
        st.markdown('<div class="fd-chat-sub">' + ("Relacionadas" if m["tipo"] == "respuesta" else "¿Te refieres a…?")
                    + '</div>', unsafe_allow_html=True)
        for j, (fid, preg) in enumerate(m["sugerencias"]):
            st.button(preg, key=f"fd_sug_{i}_{j}", on_click=_cb_preguntar_id, args=(fid, contexto))
    if m.get("contacto"):
        botones_contacto(c, "" if m.get("faq_id") == "contacto" else m.get("pregunta_usuario", ""))


# =============================================================================
# Ventana de conversación (fragmento: sólo se vuelve a ejecutar el chat)
# =============================================================================
@st.fragment
def _ventana(contexto: dict):
    if st.session_state.pop("fd_chat_navegar", False):
        st.rerun(scope="app")   # «Ir a…»: recarga completa para mostrar la nueva sección; la ventana sigue abierta
    motor = _motor()
    c = contexto["contacto"]
    seccion = st.session_state.get(KEY_SECCION)
    st.session_state.setdefault(K_HIST, [])
    st.session_state.setdefault(K_PEND, [])

    with st.container(key="fd_chat_ventana"):
        st.markdown('<div class="fd-chat-top"><b>⚡ Asistente FD Energy</b>'
                    '<span>Dudas sobre el uso del dashboard · no diagnostica fallas</span></div>',
                    unsafe_allow_html=True)
        with st.container(key="fd_chat_cuerpo"):
            mensajes = st.container(height=config.VENTANA_ALTO_MENSAJES_PX, border=False)
            pregunta = st.chat_input("Escribe tu pregunta…", key="fd_chat_input")
            a, b, d = st.columns([1.5, 1, 1], vertical_alignment="center")
            ids = POR_SECCION.get(seccion, []) + [x for x in PRINCIPALES if x not in POR_SECCION.get(seccion, [])]
            opciones = list(dict.fromkeys(ids + [f["id"] for f in motor.preguntas()] + [f"regla_R{n}" for n in range(1, 8)]))
            a.selectbox("Más preguntas", opciones, index=None, key="fd_chat_mas", placeholder="Más preguntas…",
                        format_func=motor.pregunta_de, label_visibility="collapsed",
                        on_change=_cb_mas_preguntas, args=(contexto,))
            if c.get("correo"):
                b.link_button("✉️ Asesor", _url_correo(c), width="stretch", help=f"Escribir a {c['correo']}")
            d.button("↺ Nueva", key="fd_chat_reiniciar", on_click=_cb_reiniciar, width="stretch",
                     help="Empezar una conversación nueva")

            if pregunta and pregunta.strip():
                texto = pregunta.strip()[:500]
                _registrar(texto, motor.responder(texto, contexto))

            hist = st.session_state[K_HIST]
            with mensajes:
                # Bienvenida contextual: se adapta a la sección en la que está el usuario
                with st.chat_message("assistant", avatar="⚡"):
                    st.markdown("¡Hola! Te ayudo a usar el dashboard e interpretar sus indicadores, alertas y "
                                "oportunidades." + (f" Estás en **{seccion}**:" if seccion in POR_SECCION else ""))
                    if not hist:
                        for j, fid in enumerate(ids[:4]):
                            st.button(motor.pregunta_de(fid), key=f"fd_ini_{j}", on_click=_cb_preguntar_id,
                                      args=(fid, contexto))
                ultimo = len(hist) - 1
                for i, m in enumerate(hist):
                    with st.chat_message(m["rol"], avatar="⚡" if m["rol"] == "assistant" else "👤"):
                        if m["rol"] == "assistant":
                            _mostrar_texto(m["texto"], i)
                            if i == ultimo:       # acciones sólo en la última respuesta
                                _acciones(m, i, contexto, c)
                        else:
                            st.markdown(m["texto"])

            pend = st.session_state[K_PEND]
            if pend:
                st.download_button("⬇ Preguntas sin respuesta (CSV)", pd.DataFrame(pend).to_csv(index=False).encode("utf-8-sig"),
                                   file_name="preguntas_sin_respuesta.csv", mime="text/csv", key="fd_chat_csv",
                                   type="tertiary")


def asistente_flotante(ctx=None, pipeline=None):
    """Botón 💬 fijo en la esquina inferior derecha; al pulsarlo abre la ventana del asistente.
    Se minimiza o se cierra con el mismo botón; la conversación se conserva en la sesión."""
    _css()
    contexto = construir_contexto(ctx, pipeline)
    with st.container(key="fd_chat_fab"):
        with st.popover("💬"):
            _ventana(contexto)
