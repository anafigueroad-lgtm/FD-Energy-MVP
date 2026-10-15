"""
FD Energy — Dashboard MVP (Streamlit)

Ejecutar:  streamlit run app.py
La lógica de cálculo vive en pipeline.py; este archivo sólo muestra resultados.
"""
import hashlib
import html
import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import pipeline as pl

# =============================================================================
# Configuración e identidad visual
# =============================================================================
st.set_page_config(page_title="FD Energy · MVP", page_icon="⚡", layout="wide")

_DIR = Path(__file__).parent
# Busca la base de demostración en data/ o junto a app.py (útil al subir los archivos a GitHub desde el navegador)
RUTA_LOGO = next((p for p in [_DIR / "FD_Energy_logo.png", _DIR / "FD_Energy_logo_color_transparente.png",
                              _DIR / "assets" / "FD_Energy_logo.png"] if p.exists()), None)
RUTA_DEMO = next((p for p in [_DIR / "data" / "FD_Energy_BASE_MVP.xlsx", _DIR / "FD_Energy_BASE_MVP.xlsx"] if p.exists()),
                 _DIR / "data" / "FD_Energy_BASE_MVP.xlsx")
NAVY, INK, MUTED, GRID, BAND = "#1F3A5C", "#1F2E45", "#6B7480", "#E3E7EC", "#F1F4F7"
SOLAR, IMPORT, NO_ASIG, ALERTA, OK = "#D4A72C", "#8FA3B8", "#C9CED4", "#B5523B", "#3F7D63"
PALETA = ["#1F3A5C", "#6E9C9A", "#B09A78", "#A4889B", "#7E8A97", "#4F6D8F", "#9DB4AB"]
COLOR_PRIORIDAD = {"alta": "#B5523B", "atencion": "#D4A72C", "informativa": "#7E8A97"}
NOMBRE_PRIORIDAD = {"alta": "Alta", "atencion": "Atención", "informativa": "Informativa"}
SECCIONES = ["Resumen general", "Consumo por áreas", "Equipos críticos",
             "Energía solar", "Climatización", "Alertas y oportunidades"]

st.markdown(f"""
<style>
  .block-container {{padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1400px;}}
  h1, h2, h3 {{color: {INK}; letter-spacing: -0.01em;}}
  .fd-head {{border-bottom: 1px solid {GRID}; padding-bottom: .6rem; margin-bottom: 1.1rem;}}
  .fd-head h1 {{font-size: 1.75rem; margin: 0 0 .15rem 0; padding: 0;}}
  .fd-head p {{color: {MUTED}; margin: 0; font-size: .92rem;}}
  .fd-card {{background: #fff; border: 1px solid {GRID}; border-radius: 10px; padding: 14px 16px 12px;
             min-height: 142px; border-top: 3px solid {NAVY};}}
  .fd-card.solar {{border-top-color: {SOLAR};}}
  .fd-card.neutral {{border-top-color: {IMPORT};}}
  .fd-label {{color: {MUTED}; font-size: .78rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;}}
  .fd-value {{color: {INK}; font-size: 1.65rem; font-weight: 700; line-height: 1.25; margin-top: 4px;}}
  .fd-unit {{font-size: .95rem; font-weight: 500; color: {MUTED}; margin-left: 3px;}}
  .fd-delta {{font-size: .82rem; margin-top: 6px;}}
  .fd-foot {{color: {MUTED}; font-size: .74rem; margin-top: 4px;}}
  .fd-badge {{display: inline-block; font-size: .68rem; padding: 1px 7px; border-radius: 10px;
              background: {BAND}; color: {MUTED}; margin-left: 4px; font-weight: 600;}}
  .fd-info {{background: {BAND}; border-radius: 10px; padding: 14px 18px; font-size: .88rem; color: {INK};}}
  .fd-info b {{color: {NAVY};}}
  .fd-future {{border: 1px dashed {NO_ASIG}; border-radius: 10px; padding: 12px 16px; color: {MUTED}; font-size: .85rem;}}
  [data-testid="stSidebar"] {{background: {BAND};}}
  .fd-brand {{font-size: 1.45rem; font-weight: 800; color: {NAVY}; letter-spacing: -0.02em;}}
  .fd-brand span {{color: {SOLAR};}}
  .fd-tag {{color: {MUTED}; font-size: .8rem; margin-top: 2px;}}
</style>
""", unsafe_allow_html=True)


# =============================================================================
# Formato y componentes
# =============================================================================
def num(v, dec=0):
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return "—"
    return f"{v:,.{dec}f}"


def fecha(d):
    return pd.Timestamp(d).strftime("%d/%m/%Y")


def delta_html(comp, sentido="consumo"):
    """Variación con su tipo explícito: total, referencia de promedio diario o no disponible.
    sentido='consumo': un aumento se muestra en tono de atención; 'generacion': al revés."""
    if comp is None or comp.get("tipo") is None:
        nota = html.escape(comp.get("nota", "") if comp else "")
        return f'<div class="fd-delta" style="color:{MUTED}" title="{nota}">Comparación: No disponible</div>'
    v = comp["variacion_pct"]
    sube = v > 0
    malo = sube if sentido == "consumo" else not sube
    color = ALERTA if malo and abs(v) >= 0.05 else (OK if abs(v) >= 0.05 else MUTED)
    flecha = "▲" if sube else ("▼" if v < 0 else "■")
    if comp["tipo"] == "total":
        return f'<div class="fd-delta" style="color:{color}">{flecha} {abs(v):.1f} % vs. periodo anterior</div>'
    nota = html.escape(comp.get("nota", ""))
    return (f'<div class="fd-delta" style="color:{color}" title="{nota}">{flecha} {abs(v):.1f} % '
            f'<span class="fd-badge">REF. PROMEDIO DIARIO</span></div>')


def tarjeta(col, etiqueta, valor, unidad="", delta="", pie="", clase=""):
    col.markdown(f'<div class="fd-card {clase}"><div class="fd-label">{etiqueta}</div>'
                 f'<div class="fd-value">{valor}<span class="fd-unit">{unidad}</span></div>{delta}'
                 f'<div class="fd-foot">{pie}</div></div>', unsafe_allow_html=True)


def encabezado(titulo, P):
    a, b = P["actual"]
    st.markdown(f'<div class="fd-head"><h1>{titulo}</h1>'
                f'<p>Periodo: {fecha(a)} – {fecha(b)} ({P["dias"]} días) · Datos históricos de demostración</p></div>',
                unsafe_allow_html=True)


def estilo(fig, alto=360, leyenda=True, x_fecha=False):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="Inter, -apple-system, Segoe UI, Helvetica, Arial, sans-serif", color=INK, size=12),
        hoverlabel=dict(font_size=12), showlegend=leyenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        bargap=0.25)
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID)
    if x_fecha:
        if frecuencia == "MS":
            fig.update_xaxes(tickformat="%m/%Y", dtick="M1", hoverformat="%m/%Y")
        else:
            fig.update_xaxes(tickformat="%d/%m", hoverformat="%d/%m/%Y")
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def grafica(fig):
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False, "displayModeBar": False})


def tabla(df, **kw):
    st.dataframe(df, width="stretch", hide_index=True, **kw)


def marcar_dias_sin_dato(fig, fechas, texto="Sin datos completos"):
    for f in fechas:
        fig.add_vrect(x0=pd.Timestamp(f) - pd.Timedelta(hours=12), x1=pd.Timestamp(f) + pd.Timedelta(hours=12),
                      fillcolor=NO_ASIG, opacity=0.35, line_width=0, layer="below",
                      annotation_text=texto, annotation_position="top left",
                      annotation_font=dict(size=10, color=MUTED))


def eje_tiempo(frecuencia):
    return {"D": "Fecha", "W": "Semana (inicio en lunes)", "MS": "Mes"}[frecuencia]


def aviso_variacion(df, col_tipo="variacion_tipo"):
    if (df[col_tipo] == "promedio_diario").any():
        st.caption("ⓘ Las variaciones marcadas como «Ref. promedio diario» comparan el promedio diario de los días "
                   "completos porque algún periodo tiene días incompletos; no equivalen a la variación del consumo total.")


def col_variacion(df):
    return [("—" if pd.isna(v) else f"{v:+.1f} %") + ("" if t == "total" else (" (ref.)" if t == "promedio_diario" else ""))
            if t is not None else "No disponible" for v, t in zip(df.variacion_pct, df.variacion_tipo)]


# =============================================================================
# Datos (con caché)
# =============================================================================
@st.cache_data(show_spinner="Procesando la base de datos…")
def procesar(contenido: bytes):
    return pl.procesar_base(pl.cargar_base(io.BytesIO(contenido)))


@st.cache_data(show_spinner=False)
def indicadores(_ctx, clave, ini, fin, frecuencia):
    return pl.calcular_indicadores(_ctx, ini, fin, frecuencia)


# =============================================================================
# Barra lateral: navegación y filtros globales
# =============================================================================
with st.sidebar:
    if RUTA_LOGO:
        st.image(str(RUTA_LOGO), width="stretch")
    else:
        st.markdown('<div class="fd-brand">FD <span>⚡</span> Energy</div>', unsafe_allow_html=True)
    st.markdown('<div class="fd-tag">Gestión y eficiencia energética · MVP académico</div>', unsafe_allow_html=True)
    st.write("")
    seccion = st.radio("Sección", SECCIONES, label_visibility="collapsed")
    st.divider()

    st.markdown("**Fuente de datos**")
    fuente = st.radio("Fuente de datos", ["Base de demostración", "Cargar Excel"], label_visibility="collapsed")
    contenido = None
    if fuente == "Cargar Excel":
        archivo = st.file_uploader("Excel con la misma estructura de hojas", type=["xlsx"])
        if archivo is not None:
            contenido = archivo.getvalue()
        else:
            st.caption("Mientras no cargues un archivo se usa la base de demostración.")
    if contenido is None:
        if not RUTA_DEMO.exists():
            st.error(f"No se encontró la base de demostración en {RUTA_DEMO}.")
            st.stop()
        contenido = RUTA_DEMO.read_bytes()

try:
    ctx = procesar(contenido)
except Exception as e:     # archivo con estructura distinta
    st.error(f"No fue posible procesar el archivo: {e}")
    st.stop()
clave = hashlib.md5(contenido).hexdigest()
fmin, fmax = ctx["fecha_min"].date(), ctx["fecha_max"].date()

with st.sidebar:
    st.divider()
    st.markdown("**Filtros**")
    rango = st.date_input("Periodo", value=(max(fmin, fmax - pd.Timedelta(days=29)), fmax),
                          min_value=fmin, max_value=fmax, format="DD/MM/YYYY")
    frecuencia = st.selectbox("Frecuencia de las gráficas", ["D", "W", "MS"],
                              format_func=lambda f: pl.NOMBRES_FRECUENCIA[f])
    plantas = ctx["base"]["planta"]
    st.selectbox("Planta", plantas.planta_nombre.tolist(), disabled=len(plantas) == 1,
                 help="La base de demostración contiene una sola planta.")
    st.divider()
    st.caption(f"Datos disponibles: {fecha(fmin)} – {fecha(fmax)}.  \n"
               "Información histórica: el sistema no recibe datos en tiempo real.")

if not isinstance(rango, (tuple, list)) or len(rango) < 2:
    st.info("Selecciona la fecha final del periodo en la barra lateral.")
    st.stop()
ini, fin = rango
if fin < ini:
    st.warning("La fecha final debe ser igual o posterior a la fecha inicial.")
    st.stop()

R = indicadores(ctx, clave, pd.Timestamp(ini), pd.Timestamp(fin), frecuencia)
P = R["periodos"]
cal = R["calidad"]


def aviso_calidad(medidores=None, contexto=""):
    """Advertencia cuando el periodo contiene días incompletos (opcionalmente sólo de ciertos medidores)."""
    inc = cal["incidencias"]
    if medidores is not None:
        inc = inc[inc.medidor_id.isin(medidores)]
    if inc.empty:
        return
    partes = [f"{r.medidor_id} el {fecha(r.fecha)} ({r.situacion.lower()}, {r.completitud*100:.0f} % válido)"
              for r in inc.itertuples()]
    st.warning(f"**Datos incompletos en el periodo{contexto}:** " + "; ".join(partes) +
               ". Esos días no se suman como días completos ni se estiman las horas faltantes.", icon="⚠️")


# =============================================================================
# 1. Resumen general
# =============================================================================
def vista_resumen():
    encabezado("Resumen general", P)
    rs, co, so = R["resumen"], R["costo"], R["solar"]
    aviso_calidad()

    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Consumo de planta", num(rs["consumo_planta_kwh"]), "kWh", delta_html(rs["comparacion_consumo"]),
            f'{rs["dias_balance_completo"]} de {rs["dias_periodo"]} días con balance completo')
    tarjeta(c2, "Costo energético estimado", "$" + num(co["costo_estimado_mxn"]), "MXN", delta_html(co["comparacion"]),
            f'Importación × ${num(co["costo_promedio_mxn_kwh"], 2)}/kWh ({co["fuente_costo"]})', "neutral")
    tarjeta(c3, "Generación solar", num(rs["generacion_fv_kwh"]), "kWh", delta_html(so["comparacion"], "generacion"),
            f'{rs["dias_generacion"]} de {rs["dias_periodo"]} días con lecturas del inversor', "solar")
    tarjeta(c4, "Generación / consumo", num(rs["relacion_generacion_consumo_pct"], 1), "%", "",
            f'Sobre {rs["dias_balance_completo"]} días con balance completo', "solar")
    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Importación de red", num(rs["importacion_kwh"]), "kWh", "", f'{rs["dias_importacion"]} días completos', "neutral")
    tarjeta(c2, "Exportación a red", num(rs["exportacion_kwh"]), "kWh", "", f'{rs["dias_exportacion"]} días completos', "neutral")
    tarjeta(c3, "Consumo promedio diario", num(rs["promedio_diario_kwh"]), "kWh/día", "", "Días con balance completo")
    tarjeta(c4, "Completitud de datos", num(cal["completitud_pct"], 1), "%", "",
            "Intervalos válidos / esperados (todos los medidores)", "neutral")
    for comp in (rs["comparacion_consumo"], co["comparacion"]):
        if comp["nota"]:
            st.caption("ⓘ " + comp["nota"])
            break

    st.write("")
    izq, der = st.columns([2.2, 1])
    with izq:
        st.subheader("Tendencia de consumo")
        s = R["serie_planta"]
        fig = go.Figure()
        fig.add_bar(x=s.fecha, y=s.importacion_kwh, name="Importación de red", marker_color=IMPORT,
                    hovertemplate="%{y:,.0f} kWh<extra>Importación</extra>")
        fig.add_bar(x=s.fecha, y=s.generacion_fv_kwh, name="Generación FV", marker_color=SOLAR,
                    hovertemplate="%{y:,.0f} kWh<extra>Generación FV</extra>")
        fig.add_scatter(x=s.fecha, y=s.consumo_planta_kwh, name="Consumo de planta", mode="lines+markers",
                        line=dict(color=NAVY, width=2.5), marker=dict(size=6), customdata=s[["dias_en_bloque", "advertencia"]],
                        hovertemplate="%{y:,.0f} kWh · %{customdata[0]} día(s)<br>%{customdata[1]}<extra>Consumo</extra>")
        if frecuencia == "D":
            marcar_dias_sin_dato(fig, s.loc[s.dias_incompletos > 0, "fecha"], "Balance incompleto")
        fig.update_layout(barmode="group", yaxis_title="kWh", xaxis_title=eje_tiempo(frecuencia))
        grafica(estilo(fig, 380, x_fecha=True))
        if frecuencia != "D" and (s.advertencia != "").any():
            st.caption("ⓘ " + "; ".join(f"{fecha(r.fecha)}: {r.advertencia.lower()}" for r in s[s.advertencia != ""].itertuples()))
        st.caption("El consumo de planta = importación + generación FV − exportación. Los días con balance incompleto "
                   "no se suman ni se interpretan como cero.")
    with der:
        st.subheader("Información del periodo")
        ar, al = R["areas"], R["alertas_resumen"]
        mayor = ar.iloc[0] if len(ar) else None
        ant = P["anterior"]
        st.markdown(f"""<div class="fd-info">
        <b>Periodo actual:</b> {fecha(P["actual"][0])} – {fecha(P["actual"][1])}<br>
        <b>Periodo anterior comparable:</b> {fecha(ant[0])} – {fecha(ant[1])}
        {"" if P["anterior_disponible"] else " <i>(fuera del rango de datos)</i>"}<br>
        <b>Días con balance completo:</b> {rs["dias_balance_completo"]} de {rs["dias_periodo"]}<br><br>
        <b>Área de mayor consumo:</b> {mayor.area_nombre if mayor is not None else "—"}
        ({num(mayor.participacion_pct if mayor is not None else None, 1)} % del consumo de planta)<br>
        <b>Alertas históricas detectadas:</b> {al["alertas_historicas_periodo"]}
        ({al["prioridad_alta"]} de prioridad alta)<br>
        <b>Oportunidades identificadas:</b> {len(R["oportunidades"])}
        </div>""", unsafe_allow_html=True)
        st.write("")
        st.caption(co["nota"])
        st.markdown('<div class="fd-future"><b>Funcionalidades futuras</b> (no calculadas en esta versión): '
                    'ahorros comprobados, ROI y periodo de recuperación. Requieren una línea base validada, '
                    'mediciones posteriores a una mejora y costos de inversión que la base actual no contiene.</div>',
                    unsafe_allow_html=True)

    with st.expander("Calidad de datos y validación del pipeline"):
        st.markdown("**Validación contra las tablas de referencia `calc_` del Excel** (sólo comparación; no se usan como fuente)")
        v = ctx["df_validacion_calc"]
        if len(v):
            tabla(v.rename(columns={"indicador": "Indicador", "resultado_python": "Pipeline", "resultado_calc": "Referencia calc_",
                                    "registros_comparados": "Registros", "registros_distintos": "Distintos", "estado": "Estado"}),
                  column_config={"Pipeline": st.column_config.NumberColumn(format="%.2f"),
                                 "Referencia calc_": st.column_config.NumberColumn(format="%.2f")})
            st.markdown("**Diferencias documentadas (no se forzó la coincidencia):**")
            for n in pl.NOTAS_DIFERENCIAS_CALC:
                st.markdown("- " + n)
        st.markdown("**Validación de calidad e integridad de la base**")
        tabla(ctx["validacion_inicial"])
        if len(cal["lecturas_excluidas"]):
            st.markdown("**Lecturas excluidas del periodo** (FALTANTE o INVÁLIDO; no suman energía)")
            tabla(cal["lecturas_excluidas"])
        if len(cal["lecturas_estimadas"]):
            st.markdown("**Lecturas ESTIMADAS en el periodo** (se consideran válidas, según la regla de la base)")
            tabla(cal["lecturas_estimadas"])


# =============================================================================
# 2. Consumo por áreas
# =============================================================================
def vista_areas():
    encabezado("Consumo por áreas", P)
    ar, meta = R["areas"], R["areas_meta"]
    med_areas = ctx["base"]["medidor"].query("elemento_tipo == 'area'").medidor_id.tolist()
    med_balance = ctx["base"]["medidor"].query("tipo_medicion != 'submedidor'").medidor_id.tolist()
    aviso_calidad(med_areas + med_balance, " (balance de planta o submedidores de área)")
    if ar.empty:
        st.info("No hay información de áreas para el periodo seleccionado.")
        return

    mi = R["area_mayor_incremento"]
    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Área de mayor consumo", ar.iloc[0].area_nombre, "",
            f'<div class="fd-delta" style="color:{NAVY}">{num(ar.iloc[0].participacion_pct, 1)} % del consumo de planta</div>',
            f'{num(ar.iloc[0].kwh_base)} kWh en días comparables')
    tarjeta(c2, "Mayor incremento", mi["area_nombre"] if mi else "—", "",
            f'<div class="fd-delta" style="color:{ALERTA}">▲ {mi["variacion_pct"]:.1f} % vs. periodo anterior</div>' if mi else "",
            "Variación del total; base ≥ mínimo de la regla R1" if mi else "Sin aumentos comparables en el periodo")
    tarjeta(c3, "Consumo no asignado", num(meta["no_asignado_kwh"]), "kWh",
            f'<div class="fd-delta" style="color:{ALERTA if meta["residual_negativo"] else MUTED}">'
            f'{num(meta["no_asignado_pct"], 1)} % del consumo de planta</div>',
            "Consumo de planta − Σ áreas medidas", "neutral")
    tarjeta(c4, "Días comparables", f'{meta["dias_base"]} / {meta["dias_periodo"]}', "", "",
            "Balance y todas las áreas completas", "neutral")
    if meta["residual_negativo"]:
        st.error("La suma de áreas supera el consumo de planta en los días comparables (residual negativo). "
                 "Requiere revisar la configuración o calibración de los medidores; el valor no se ajusta.")
    if len(meta["dias_excluidos"]):
        st.caption("ⓘ Días excluidos de participaciones y no asignado: " + "; ".join(
            f"{fecha(r.fecha)} ({r.motivo_sin_calculo.lower()})" for r in meta["dias_excluidos"].itertuples()))

    st.write("")
    izq, der = st.columns([1, 1.35])
    with izq:
        st.subheader("Participación en el consumo de planta")
        d = pd.concat([ar[["area_nombre", "kwh_base", "participacion_pct"]],
                       pd.DataFrame([{"area_nombre": "No asignado", "kwh_base": meta["no_asignado_kwh"],
                                      "participacion_pct": meta["no_asignado_pct"]}])], ignore_index=True).iloc[::-1]
        colores = [NO_ASIG if n == "No asignado" else PALETA[0] for n in d.area_nombre]
        fig = go.Figure(go.Bar(x=d.kwh_base, y=d.area_nombre, orientation="h", marker_color=colores,
                               text=[f"{p:.1f} %" if pd.notna(p) else "" for p in d.participacion_pct], textposition="outside",
                               hovertemplate="%{y}: %{x:,.0f} kWh<extra></extra>", cliponaxis=False))
        fig.update_layout(xaxis_title=f'kWh ({meta["dias_base"]} días comparables)')
        fig.update_xaxes(range=[0, d.kwh_base.max() * 1.2])
        grafica(estilo(fig, 340, leyenda=False))
        st.caption("Porcentajes calculados con los mismos días en el numerador y el denominador. "
                   "«No asignado» es un indicador de balance (cargas sin submedidor y pérdidas), no un área.")
    with der:
        st.subheader("Evolución por área")
        s = R["areas_serie"]
        fig = go.Figure()
        for i, (nom, g) in enumerate(s.groupby("area_nombre", sort=False)):
            fig.add_scatter(x=g.fecha, y=g.kwh, name=nom, mode="lines+markers", line=dict(color=PALETA[i % len(PALETA)], width=2),
                            marker=dict(size=5), connectgaps=False,
                            hovertemplate="%{y:,.0f} kWh<extra>" + nom + "</extra>")
        fig.update_layout(yaxis_title="kWh", xaxis_title=eje_tiempo(frecuencia))
        grafica(estilo(fig, 340, x_fecha=True))
        if (s.dias_incompletos > 0).any():
            st.caption("ⓘ Los días parciales quedan fuera de la serie (en vista diaria aparecen como huecos).")

    st.subheader("Detalle por área")
    t = ar.assign(variacion=col_variacion(ar))
    tabla(t[["area_nombre", "kwh_base", "participacion_pct", "kwh_dias_completos", "dias_completos", "dias_parciales",
             "variacion", "ranking"]].rename(columns={
        "area_nombre": "Área", "kwh_base": "kWh (días comparables)", "participacion_pct": "Participación %",
        "kwh_dias_completos": "kWh (días completos del área)", "dias_completos": "Días completos",
        "dias_parciales": "Días parciales", "variacion": "Variación vs. anterior", "ranking": "Ranking"}),
        column_config={"kWh (días comparables)": st.column_config.NumberColumn(format="%.0f"),
                       "Participación %": st.column_config.NumberColumn(format="%.1f"),
                       "kWh (días completos del área)": st.column_config.NumberColumn(format="%.0f"),
                       "Ranking": st.column_config.NumberColumn(format="%d")})
    aviso_variacion(ar)


# =============================================================================
# 3. Equipos críticos
# =============================================================================
def vista_equipos():
    encabezado("Equipos críticos", P)
    ce = R["conteo_equipos"]
    med_eq = ctx["base"]["medidor"].query("elemento_tipo == 'equipo'").medidor_id.tolist()
    aviso_calidad(med_eq, " (medidores de equipos)")

    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Equipos registrados", ce["equipos_registrados"], "", "", "Tabla equipo", "neutral")
    tarjeta(c2, "Con medición individual", ce["equipos_con_medicion"], "", "", "Únicos con consumo calculado")
    tarjeta(c3, "Prioritarios", ce["equipos_prioritarios"], "", "",
            f'{ce["prioritarios_con_medicion"]} con medición individual')
    mi = R["equipo_mayor_incremento"]
    tarjeta(c4, "Mayor incremento", mi["equipo_nombre"] if mi else "—", "",
            f'<div class="fd-delta" style="color:{ALERTA}">▲ {mi["variacion_pct"]:.1f} % vs. periodo anterior</div>' if mi else "",
            "Equipos medidos; variación del total" if mi else "Sin aumentos comparables en el periodo")
    if ce["prioritarios_sin_medicion"]:
        st.caption("ⓘ Equipos prioritarios sin medición individual (no se estima su consumo): "
                   + ", ".join(ce["prioritarios_sin_medicion"]))

    alcance = st.radio("Equipos mostrados", ["Prioritarios", "Todos los equipos con medición"], horizontal=True)
    eq = R["equipos_prioritarios"] if alcance == "Prioritarios" else R["equipos_medidos"]
    if eq.empty:
        st.info("No hay equipos con medición para el criterio seleccionado.")
        return
    ids = set(eq.equipo_id)

    izq, der = st.columns([1, 1.35])
    with izq:
        st.subheader("Consumo por equipo")
        d = eq.iloc[::-1]
        fig = go.Figure(go.Bar(x=d.kwh_dias_completos, y=d.equipo_nombre, orientation="h", marker_color=NAVY,
                               text=[num(v) for v in d.kwh_dias_completos], textposition="outside", cliponaxis=False,
                               customdata=d[["area_nombre", "dias_completos"]],
                               hovertemplate="%{y} (%{customdata[0]})<br>%{x:,.0f} kWh en %{customdata[1]} días completos<extra></extra>"))
        fig.update_layout(xaxis_title="kWh (días completos)")
        fig.update_xaxes(range=[0, d.kwh_dias_completos.max() * 1.2])
        grafica(estilo(fig, 320, leyenda=False))
    with der:
        st.subheader("Evolución por equipo")
        s = R["equipos_serie"][R["equipos_serie"].equipo_id.isin(ids)]
        fig = go.Figure()
        for i, (nom, g) in enumerate(s.groupby("equipo_nombre", sort=False)):
            fig.add_scatter(x=g.fecha, y=g.kwh, name=nom, mode="lines+markers", line=dict(color=PALETA[i % len(PALETA)], width=2),
                            marker=dict(size=5), hovertemplate="%{y:,.0f} kWh<extra>" + nom + "</extra>")
        fig.update_layout(yaxis_title="kWh", xaxis_title=eje_tiempo(frecuencia))
        grafica(estilo(fig, 320, x_fecha=True))

    st.subheader("Indicadores para identificar posibles desviaciones")
    al = R["alertas"]
    n_al = al[al.elemento_tipo == "equipo"].groupby("elemento_id").size()
    t = eq.assign(variacion=col_variacion(eq), alertas=eq.equipo_id.map(n_al).fillna(0).astype(int))
    tabla(t[["equipo_nombre", "area_nombre", "kwh_dias_completos", "promedio_diario_kwh", "participacion_en_area_pct",
             "variacion", "alertas", "dias_incompletos"]].rename(columns={
        "equipo_nombre": "Equipo", "area_nombre": "Área", "kwh_dias_completos": "kWh (días completos)",
        "promedio_diario_kwh": "Promedio diario kWh", "participacion_en_area_pct": "% del consumo de su área",
        "variacion": "Variación vs. anterior", "alertas": "Alertas en el periodo",
        "dias_incompletos": "Días incompletos"}),
        column_config={"kWh (días completos)": st.column_config.NumberColumn(format="%.0f"),
                       "Promedio diario kWh": st.column_config.NumberColumn(format="%.1f"),
                       "% del consumo de su área": st.column_config.NumberColumn(format="%.1f")})
    aviso_variacion(eq)
    st.caption("La participación en el área se calcula sólo con días en que el equipo y el submedidor de su área "
               "están completos. Una variación o alerta indica un comportamiento a revisar, no una falla.")


# =============================================================================
# 4. Energía solar
# =============================================================================
def vista_solar():
    encabezado("Energía solar", P)
    so = R["solar"]
    med_fv = ctx["base"]["medidor"].query("tipo_medicion == 'generacion_fv'").medidor_id.tolist()
    sd = R["solar_diario"]
    sin = sd[sd.estado_fv == "Sin lecturas"]
    if len(sin):
        st.warning(f"**Faltan lecturas del medidor fotovoltaico ({', '.join(med_fv)})** el "
                   + ", ".join(fecha(f) for f in sin.fecha) +
                   ". Esos días no se interpretan como generación cero: se excluyen de los totales y comparaciones.", icon="⚠️")
    if not len(sin):
        aviso_calidad(med_fv, " (inversor)")
    cero = sd[sd.estado_fv == "Cero medido"]
    if len(cero):
        st.info("**Generación cero medida** el " + ", ".join(fecha(f) for f in cero.fecha) +
                ": el inversor reportó 0 kWh con el 100 % de sus intervalos. Es un dato registrado (no un faltante) "
                "y la regla R4 lo identifica para revisión.", icon="ℹ️")

    c1, c2, c3 = st.columns(3)
    tarjeta(c1, "Generación FV", num(so["generacion_fv_kwh"]), "kWh", delta_html(so["comparacion"], "generacion"),
            f'{so["dias_generacion_registrada"]} de {so["dias_periodo"]} días con lecturas completas', "solar")
    tarjeta(c2, "Promedio diario", num(so["promedio_diario_kwh"]), "kWh/día", "", "Días con lecturas completas", "solar")
    tarjeta(c3, "Rendimiento específico", num(so["rendimiento_especifico_kwh_kwp"], 1), "kWh/kWp", "",
            f'Generación del periodo / {num(so["capacidad_kwp"])} kWp instalados', "solar")
    st.write("")
    c1, c2, c3 = st.columns(3)
    tarjeta(c1, "Generación / consumo de planta", num(so["relacion_generacion_consumo_pct"], 1), "%", "",
            f'{so["dias_base_balance"]} días con balance completo')
    tarjeta(c2, "FV autoconsumida", num(so["fraccion_autoconsumida_pct"], 1), "%", "",
            f'{num(so["fv_autoconsumida_kwh"])} kWh = generación − exportación')
    tarjeta(c3, "Exportación a red", num(so["exportacion_base_kwh"]), "kWh", "", "Excedente entregado a la red", "neutral")
    if so["comparacion"]["nota"]:
        st.caption("ⓘ " + so["comparacion"]["nota"])

    st.subheader("Generación fotovoltaica")
    if frecuencia == "D":
        fig = go.Figure()
        fig.add_bar(x=sd.fecha, y=sd.generacion_fv_kwh_completa, name="Generación FV", marker_color=SOLAR,
                    hovertemplate="%{y:,.0f} kWh<extra>Generación FV</extra>")
        fig.add_scatter(x=sd.fecha, y=sd.exportacion_kwh, name="Exportación a red", mode="lines",
                        line=dict(color=NAVY, width=2), hovertemplate="%{y:,.0f} kWh<extra>Exportación</extra>")
        if len(cero):
            fig.add_scatter(x=cero.fecha, y=[0] * len(cero), mode="markers", name="Cero medido",
                            marker=dict(symbol="diamond", size=11, color=ALERTA), hovertemplate="0 kWh medido<extra></extra>")
        marcar_dias_sin_dato(fig, sin.fecha, "Sin lecturas")
    else:
        s = R["serie_planta"]
        fig = go.Figure()
        fig.add_bar(x=s.fecha, y=s.generacion_fv_kwh, name="Generación FV", marker_color=SOLAR,
                    customdata=s[["dias_en_bloque"]], hovertemplate="%{y:,.0f} kWh · %{customdata[0]} día(s)<extra></extra>")
        fig.add_scatter(x=s.fecha, y=s.exportacion_kwh, name="Exportación a red", mode="lines+markers",
                        line=dict(color=NAVY, width=2))
        if len(sin):
            st.caption("ⓘ Los bloques que incluyen días sin lecturas FV suman sólo los días con lecturas completas.")
    fig.update_layout(yaxis_title="kWh", xaxis_title=eje_tiempo(frecuencia))
    grafica(estilo(fig, 360, x_fecha=True))
    st.caption("Indicadores calculables con la base actual. La relación generación/consumo no es autoconsumo; "
               "sin datos de irradiancia no es posible atribuir la causa de una baja generación.")


# =============================================================================
# 5. Climatización
# =============================================================================
def vista_clima():
    encabezado("Climatización", P)
    hv, eqs, serie = R["hvac"], R["hvac_equipos"], R["hvac_serie"]
    areas = ctx["base"]["area"]
    ids_hv = areas.loc[areas.uso_predominante == "climatizacion", "area_id"]
    med = ctx["base"]["medidor"]
    med_hv = med[(med.elemento_tipo == "area") & med.elemento_id.isin(ids_hv)].medidor_id.tolist()
    aviso_calidad(med_hv, " (tablero de climatización)")

    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Consumo de climatización", num(hv["consumo_climatizacion_kwh"]), "kWh", delta_html(hv["comparacion"]),
            f'{hv["dias_completos"]} de {hv["dias_periodo"]} días completos')
    tarjeta(c2, "Participación en planta", num(hv["participacion_planta_pct"], 1), "%", "",
            f'{hv["dias_base_participacion"]} días comparables')
    tarjeta(c3, "Promedio diario", num(hv["promedio_diario_kwh"]), "kWh/día", "", "Días completos")
    tarjeta(c4, "Equipo HVAC de mayor consumo", hv["equipo_hvac_mayor_consumo"] or "—", "", "",
            "Entre equipos con medición individual", "neutral")
    if hv["comparacion"]["nota"]:
        st.caption("ⓘ " + hv["comparacion"]["nota"])

    izq, der = st.columns([1.35, 1])
    with izq:
        st.subheader("Periodo actual vs. periodo anterior")
        fig = go.Figure()
        for nom, color, dash in [("Periodo actual", NAVY, "solid"), ("Periodo anterior", "#7E8A97", "dash")]:
            g = serie[serie.periodo == nom]
            if len(g):
                fig.add_scatter(x=g.dia_relativo, y=g.kwh_completo, name=nom, mode="lines+markers",
                                line=dict(color=color, width=2.5 if nom == "Periodo actual" else 1.8, dash=dash),
                                customdata=g.fecha.dt.strftime("%d/%m/%Y"),
                                hovertemplate="%{customdata}: %{y:,.0f} kWh<extra>" + nom + "</extra>")
        fig.update_layout(xaxis_title="Día del periodo", yaxis_title="kWh/día")
        grafica(estilo(fig, 340))
        if not P["anterior_disponible"]:
            st.caption("ⓘ El periodo anterior queda fuera del rango de datos; sólo se muestra el actual.")
    with der:
        st.subheader("Equipos HVAC medidos")
        if eqs.empty:
            st.info("No hay equipos de climatización con medición individual.")
        else:
            d = eqs.iloc[::-1]
            fig = go.Figure(go.Bar(x=d.participacion_en_area_pct, y=d.equipo_nombre, orientation="h", marker_color=PALETA[1],
                                   text=[f"{v:.1f} %" for v in d.participacion_en_area_pct], textposition="outside",
                                   cliponaxis=False, customdata=d[["kwh_dias_completos"]],
                                   hovertemplate="%{y}: %{x:.1f} % del tablero (%{customdata[0]:,.0f} kWh)<extra></extra>"))
            fig.update_layout(xaxis_title="% del consumo del tablero de climatización", xaxis_range=[0, 100])
            grafica(estilo(fig, 260, leyenda=False))
            sin_med = [e for e in R["conteo_equipos"]["equipos_sin_medicion"]
                       if e in ctx["base"]["equipo"].query("categoria_uso == 'climatizacion'").equipo_nombre.tolist()]
            if sin_med:
                st.caption("Sin medición individual: " + ", ".join(sin_med) +
                           ". No se calcula la distribución por zonas (nota N1 de la base).")

    st.subheader("Elementos de climatización que requieren atención")
    al = R["alertas"]
    ids_eq_hv = set(ctx["base"]["equipo"].query("categoria_uso == 'climatizacion'").equipo_id)
    a = al[((al.elemento_tipo == "area") & al.elemento_id.isin(ids_hv)) |
           ((al.elemento_tipo == "equipo") & al.elemento_id.isin(ids_eq_hv)) |
           ((al.elemento_tipo == "medidor") & al.elemento_id.isin(med_hv))]
    if a.empty:
        st.success("No se detectaron alertas históricas de climatización en el periodo seleccionado.", icon="✅")
    else:
        tabla(a.assign(prioridad=a.prioridad.map(NOMBRE_PRIORIDAD))[["fecha_evaluada", "regla_id", "prioridad", "elemento", "mensaje"]]
              .rename(columns={"fecha_evaluada": "Fecha evaluada", "regla_id": "Regla", "prioridad": "Prioridad",
                               "elemento": "Elemento", "mensaje": "Condición detectada"}),
              column_config={"Fecha evaluada": st.column_config.DateColumn(format="DD/MM/YYYY")})
    if len(eqs):
        aviso_variacion(eqs)


# =============================================================================
# 6. Alertas y oportunidades
# =============================================================================
def vista_alertas():
    encabezado("Alertas y oportunidades", P)
    al, res, op = R["alertas"], R["alertas_resumen"], R["oportunidades"]
    st.markdown(f'<div class="fd-info">Las <b>alertas históricas</b> son observaciones automáticas: condiciones que '
                f'superaron un umbral de las reglas R1–R6 en los datos del periodo (según su fecha evaluada). '
                f'Las <b>oportunidades</b> (regla R7) son observaciones que podrían justificar una revisión. '
                f'Ninguna diagnostica fallas ni sustituye una evaluación técnica especializada.</div>', unsafe_allow_html=True)
    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    tarjeta(c1, "Alertas históricas detectadas", res["alertas_historicas_periodo"], "", "", "Con fecha evaluada en el periodo")
    tarjeta(c2, "Prioridad alta", res["prioridad_alta"], "", "", f'{res["prioridad_atencion"]} de atención · '
            f'{res["informativas"]} informativas', "neutral")
    tarjeta(c3, "Área con más alertas", res["area_con_mas_alertas"] or "—", "", "", "Incluye alertas de sus equipos", "neutral")
    tarjeta(c4, "Oportunidades identificadas", len(op), "", "", "Regla R7 · requieren revisión técnica", "solar")

    t1, t2, t3 = st.tabs(["Alertas históricas detectadas", "Oportunidades identificadas", "Reglas evaluadas"])
    with t1:
        if al.empty:
            st.success("No se detectaron alertas históricas en el periodo seleccionado.", icon="✅")
        else:
            g = al.groupby(["fecha_evaluada", "prioridad"]).size().reset_index(name="n")
            fig = go.Figure()
            for pr in ["alta", "atencion", "informativa"]:
                x = g[g.prioridad == pr]
                if len(x):
                    fig.add_bar(x=x.fecha_evaluada, y=x.n, name=NOMBRE_PRIORIDAD[pr], marker_color=COLOR_PRIORIDAD[pr])
            fig.update_layout(barmode="stack", yaxis_title="Alertas", xaxis_title="Fecha evaluada", yaxis_dtick=1)
            grafica(estilo(fig, 240, x_fecha=True))
            prios = st.multiselect("Prioridad", ["alta", "atencion", "informativa"], default=["alta", "atencion", "informativa"],
                                   format_func=NOMBRE_PRIORIDAD.get)
            v = al[al.prioridad.isin(prios)]
            v = v.assign(area=v.area.fillna("—"))
            tabla(v.assign(prioridad=v.prioridad.map(NOMBRE_PRIORIDAD))[
                ["fecha_evaluada", "regla_id", "regla", "prioridad", "elemento", "area", "mensaje", "nota_cobertura"]].rename(columns={
                    "fecha_evaluada": "Fecha evaluada", "regla_id": "Regla", "regla": "Descripción de la regla",
                    "prioridad": "Prioridad", "elemento": "Elemento", "area": "Área", "mensaje": "Condición detectada",
                    "nota_cobertura": "Nota de cobertura"}),
                column_config={"Fecha evaluada": st.column_config.DateColumn(format="DD/MM/YYYY"),
                               "Condición detectada": st.column_config.TextColumn(width="large")})
            st.caption("Las reglas de variación comparan bloques consecutivos de 7 días alineados al último día de datos; "
                       "la fecha evaluada es el último día del bloque. Fecha de procesamiento de la base: "
                       f'{ctx["momento_procesamiento"]:%d/%m/%Y %H:%M}.')
    with t2:
        if op.empty:
            st.info("No se identificaron oportunidades con la regla R7 en el periodo (participación ≥ 20 % y aumento ≥ 10 % "
                    "con variación del total disponible).")
        else:
            st.caption("Recomendaciones que **requieren revisión técnica** antes de tomar decisiones.")
            for r in op.itertuples():
                st.markdown(f'<div class="fd-card solar" style="margin-bottom:10px"><div class="fd-label">'
                            f'{"Área" if r.elemento_tipo == "area" else "Equipo"} · {html.escape(r.elemento)}</div>'
                            f'<div style="margin-top:6px">{html.escape(r.observacion)}</div>'
                            f'<div class="fd-foot">Participación sobre: {html.escape(r.base_participacion)} · Regla {r.regla_id}</div></div>',
                            unsafe_allow_html=True)
    with t3:
        tabla(ctx["df_estado_reglas"].rename(columns={"regla_id": "Regla", "tipo_regla": "Tipo", "descripcion": "Descripción",
                                                      "estado": "Estado", "alertas_historicas": "Alertas en todo el histórico",
                                                      "nota": "Nota"}))
        ne = pl.filtrar(ctx["df_no_evaluados"], P["actual"], col="fecha_evaluada")
        if len(ne):
            st.markdown("**Evaluaciones omitidas en el periodo por falta de datos suficientes**")
            tabla(ne.rename(columns={"regla_id": "Regla", "elemento_id": "Elemento", "fecha_evaluada": "Fecha evaluada",
                                     "motivo": "Motivo"}),
                  column_config={"Fecha evaluada": st.column_config.DateColumn(format="DD/MM/YYYY")})


VISTAS = {"Resumen general": vista_resumen, "Consumo por áreas": vista_areas, "Equipos críticos": vista_equipos,
          "Energía solar": vista_solar, "Climatización": vista_clima, "Alertas y oportunidades": vista_alertas}
VISTAS[seccion]()

st.markdown(f'<div style="margin-top:2.5rem;color:{MUTED};font-size:.75rem;border-top:1px solid {GRID};padding-top:.6rem">'
            'FD Energy · MVP académico en desarrollo. Procesa datos históricos de demostración; no recibe datos en tiempo real, '
            'no ha sido validado con clientes y sus observaciones no sustituyen un diagnóstico técnico especializado.</div>',
            unsafe_allow_html=True)
