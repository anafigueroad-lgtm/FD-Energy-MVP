"""
FD Energy — Pipeline de procesamiento del MVP (versión para Streamlit)

Adaptado del notebook «FD_Energy_Pipeline_MVP». Se conservan las fórmulas y las reglas
R1–R7; los cambios respecto al notebook están marcados con «[CAMBIO Cx]» y se resumen aquí:

  C1  El balance diario ya no convierte en 0 un medidor sin lecturas (antes: pivot_table
      con aggfunc="sum"). Ahora se distingue un cero medido de la ausencia de información.
  C2  Sin variables globales ni dependencia de Google Colab. Todo el estado vive en un
      diccionario «ctx» que produce procesar_base() y consume calcular_indicadores().
  C3  Participaciones (áreas y climatización) con base de días comparables: el numerador y
      el denominador usan exactamente los mismos días, en los que todos los medidores
      involucrados tienen el 100 % de intervalos válidos. No se fuerza a sumar 100 %.
  C4  Variaciones: total si ambos periodos están completos; «referencia de promedio diario»
      (identificada como tal) si hay días incompletos pero cobertura ≥ COBERTURA_MIN_REF;
      «No disponible» en otro caso.
  C5  Los días parciales se conservan en las tablas diarias (columna «kwh»), pero los
      indicadores usan «kwh_completo», que sólo incluye días con 100 % de intervalos válidos.
      No se estiman horas faltantes.
  C6  Alertas evaluadas sobre el histórico con estado «historica»; el dashboard muestra sólo
      las que tienen fecha_evaluada dentro del periodo. No hay «alertas activas».
  C7  Notas de variación según la causa real (periodo anterior fuera de rango o días
      incompletos).

Las tablas calc_ del Excel NO se usan como fuente; sólo se leen en validar_contra_calc().
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# =============================================================================
# 1. Configuración
# =============================================================================
TABLAS_BASE = ["empresa", "planta", "area", "equipo", "sistema_fv", "medidor",
               "tarifa_costo", "regla_alerta", "lote_carga", "lectura", "alerta_generada"]
CALIDAD_VALIDA = ["MEDIDO", "ESTIMADO"]   # calidades que suman energía
TOL_ABS = 0.01                            # tolerancia para validar contra calc_ (kWh o MXN)

# [CAMBIO C4] Cobertura mínima de días completos (en cada periodo) para informar una
# variación de referencia basada en el promedio diario. Valor conservador: con 7 días,
# admite un día incompleto pero no dos.
COBERTURA_MIN_REF = 0.80

ESTADO_ALERTA = "historica"               # [CAMBIO C6] antes: "activa"

NOMBRES_FRECUENCIA = {"D": "Diaria", "W": "Semanal", "MS": "Mensual"}


def _freq(frecuencia):
    """Semanas de lunes a domingo etiquetadas con el lunes; meses con el día 1."""
    if frecuencia == "W":
        return {"freq": "W-MON", "label": "left", "closed": "left"}
    return {"freq": frecuencia}


# =============================================================================
# 2. Carga
# =============================================================================
def cargar_base(origen) -> dict[str, pd.DataFrame]:
    """Lee todas las hojas del Excel. «origen» puede ser una ruta o un objeto tipo archivo."""
    hojas = pd.read_excel(origen, sheet_name=None)
    faltan = [t for t in TABLAS_BASE if t not in hojas]
    if faltan:
        raise ValueError("El archivo no contiene las hojas requeridas: " + ", ".join(faltan))
    return hojas


# =============================================================================
# 3. Validación de calidad e integridad (sección 4 del notebook, sin cambios de lógica)
# =============================================================================
def validar_base(base: dict, lec: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    med, eq = base["medidor"], base["equipo"]
    val = []

    def registrar(grupo, prueba, ok, detalle):
        val.append({"grupo": grupo, "prueba": prueba, "resultado": "OK" if ok else "REVISAR", "detalle": detalle})

    registrar("Lecturas", "Número total de lecturas", True, f"{len(lec):,}")
    registrar("Lecturas", "Rango de fechas", True, f"{lec.timestamp_inicio.min()} a {lec.timestamp_inicio.max()}")
    pasos = lec.sort_values(["medidor_id", "timestamp_inicio"]).groupby("medidor_id").timestamp_inicio.diff().dropna()
    registrar("Lecturas", "Frecuencia temporal", pasos.mode()[0] == pd.Timedelta("1h"),
              f"Paso más frecuente: {pasos.mode()[0]}; pasos distintos de 1 h: {(pasos != pd.Timedelta('1h')).sum()}")
    registrar("Lecturas", "Medidores con lecturas", True, f"{lec.medidor_id.nunique()} de {len(med)} registrados")

    for t, k in [("empresa", "empresa_id"), ("planta", "planta_id"), ("area", "area_id"), ("equipo", "equipo_id"),
                 ("medidor", "medidor_id"), ("sistema_fv", "sistema_fv_id"), ("regla_alerta", "regla_id"),
                 ("lote_carga", "lote_carga_id")]:
        n = base[t][k].duplicated().sum()
        registrar("Duplicados", f"IDs duplicados en {t}", n == 0, f"{n}")
    n = lec.duplicated(["medidor_id", "timestamp_inicio"]).sum()
    registrar("Duplicados", "Lecturas duplicadas (medidor, timestamp)", n == 0, f"{n}")

    registrar("Calidad", "Distribución de calidad_dato", True, str(lec.calidad_dato.value_counts().to_dict()))
    neg = lec[lec.energia_intervalo_kwh < 0]
    registrar("Calidad", "Valores negativos", (neg.calidad_dato == "INVALIDO").all(),
              f"{len(neg)} negativos; calidad: {neg.calidad_dato.unique().tolist()}")
    nul = lec[lec.energia_intervalo_kwh.isna()]
    registrar("Calidad", "Valores faltantes (energía vacía)", (nul.calidad_dato == "FALTANTE").all(),
              f"{len(nul)} vacíos; calidad: {nul.calidad_dato.unique().tolist()}")
    lim = med.set_index("medidor_id").limite_max_kwh_intervalo
    sobre = lec[lec.medidor_id.map(lim).notna() & (lec.energia_intervalo_kwh > lec.medidor_id.map(lim))]
    registrar("Calidad", "Valores sobre limite_max_kwh_intervalo", (sobre.calidad_dato == "INVALIDO").all(),
              f"{len(sobre)} lecturas; calidad: {sobre.calidad_dato.unique().tolist()}")

    registrar("Integridad", "lectura.medidor_id existe en medidor", lec.medidor_id.isin(med.medidor_id).all(), "")
    registrar("Integridad", "lectura.lote_carga_id existe en lote_carga",
              lec.lote_carga_id.isin(base["lote_carga"].lote_carga_id).all(), "")
    registrar("Integridad", "area.planta_id existe en planta", base["area"].planta_id.isin(base["planta"].planta_id).all(), "")
    registrar("Integridad", "equipo.area_id existe en area", eq.area_id.isin(base["area"].area_id).all(), "")
    registrar("Integridad", "planta.empresa_id existe en empresa",
              base["planta"].empresa_id.isin(base["empresa"].empresa_id).all(), "")
    ref = {"planta": base["planta"].planta_id, "area": base["area"].area_id, "equipo": eq.equipo_id,
           "sistema_fv": base["sistema_fv"].sistema_fv_id}
    ok_el = all(r.elemento_id in set(ref.get(r.elemento_tipo, [])) for r in med.itertuples())
    registrar("Integridad", "medidor.elemento_id existe según elemento_tipo", ok_el, "")
    registrar("Integridad", "medidor_padre_id existe en medidor", med.medidor_padre_id.dropna().isin(med.medidor_id).all(), "")

    inicio, fin = lec.timestamp_inicio.min(), lec.timestamp_inicio.max()
    partes = []
    for m, fa, iv in med[["medidor_id", "fecha_alta", "intervalo_min"]].itertuples(index=False):
        esperado = pd.date_range(max(pd.Timestamp(fa), inicio), fin, freq=f"{int(iv)}min")
        presentes = lec.loc[lec.medidor_id == m, "timestamp_inicio"]
        partes.append(pd.DataFrame({"medidor_id": m, "timestamp_inicio": esperado.difference(presentes)}))
    huecos = pd.concat(partes, ignore_index=True)
    registrar("Completitud", "Intervalos esperados sin fila (huecos)", len(huecos) == 0,
              f"{len(huecos)} intervalos ausentes" + (": " + ", ".join(
                  f"{m} {g.timestamp_inicio.min():%Y-%m-%d}" for m, g in huecos.groupby("medidor_id")) if len(huecos) else ""))
    return pd.DataFrame(val), huecos


# =============================================================================
# 4. Procesamiento diario
# =============================================================================
def preparar_lecturas(lec: pd.DataFrame, med: pd.DataFrame) -> pd.DataFrame:
    lec_rol = lec.merge(med[["medidor_id", "planta_id", "tipo_medicion", "elemento_tipo", "elemento_id",
                             "medidor_padre_id", "intervalo_min"]], on="medidor_id", how="left", validate="many_to_one")
    lec_rol["fecha"] = lec_rol.timestamp_inicio.dt.normalize()
    lec_rol["es_valida"] = lec_rol.calidad_dato.isin(CALIDAD_VALIDA)
    lec_rol["kwh_valido"] = lec_rol.energia_intervalo_kwh.where(lec_rol.es_valida)   # NaN si no es válida (nunca 0)
    return lec_rol


def energia_diaria_medidor(lec_rol: pd.DataFrame, med: pd.DataFrame) -> pd.DataFrame:
    """Energía diaria por medidor sobre una malla completa de días (incluye días sin filas).
    kwh = Σ lecturas válidas (NaN si no hay ninguna); completitud = intervalos válidos / esperados.
    [CAMBIO C5] dia_completo = completitud ≥ 1; kwh_completo = kwh sólo en días completos."""
    fechas = pd.date_range(lec_rol.fecha.min(), lec_rol.fecha.max(), freq="D")
    malla = pd.MultiIndex.from_product([med.medidor_id, fechas], names=["medidor_id", "fecha"]).to_frame(index=False)
    malla = malla.merge(med[["medidor_id", "planta_id", "tipo_medicion", "elemento_tipo", "elemento_id",
                             "intervalo_min", "fecha_alta"]], on="medidor_id")
    malla = malla[malla.fecha >= pd.to_datetime(malla.fecha_alta)]
    agg = lec_rol.groupby(["medidor_id", "fecha"]).agg(
        kwh=("kwh_valido", lambda s: s.sum(min_count=1)),
        intervalos_validos=("es_valida", "sum"),
        intervalos_estimados=("calidad_dato", lambda s: (s == "ESTIMADO").sum()),
        intervalos_no_validos=("es_valida", lambda s: (~s).sum()),
        filas_recibidas=("es_valida", "size")).reset_index()
    d = malla.merge(agg, on=["medidor_id", "fecha"], how="left")
    for c in ["intervalos_validos", "intervalos_estimados", "intervalos_no_validos", "filas_recibidas"]:
        d[c] = d[c].fillna(0).astype(int)
    d["intervalos_esperados"] = (24 * 60 // d.intervalo_min).astype(int)
    d["completitud"] = d.intervalos_validos / d.intervalos_esperados
    d["dia_completo"] = d.completitud >= 1
    d["kwh_completo"] = d.kwh.where(d.dia_completo)
    return d.drop(columns=["intervalo_min", "fecha_alta"]).reset_index(drop=True)


def balance_planta_diario(df_ed: pd.DataFrame) -> pd.DataFrame:
    """Balance diario: consumo = importación + generación − exportación.
    [CAMBIO C1] Se agrega con groupby(...).sum(min_count=1) y pivot (sin aggfunc), de modo que
    un medidor sin lecturas válidas queda como NaN y no como 0. Un 0 medido conserva su valor 0
    porque su completitud es 100 %."""
    roles = {"red_importacion": "importacion", "red_exportacion": "exportacion", "generacion_fv": "generacion_fv"}
    r = df_ed[df_ed.tipo_medicion.isin(roles)]
    g = (r.groupby(["planta_id", "fecha", "tipo_medicion"])
         .agg(kwh=("kwh", lambda s: s.sum(min_count=1)), comp=("completitud", "min")).reset_index())
    kwh = g.pivot(index=["planta_id", "fecha"], columns="tipo_medicion", values="kwh")
    comp = g.pivot(index=["planta_id", "fecha"], columns="tipo_medicion", values="comp")
    b = pd.DataFrame(index=kwh.index)
    for rol, nombre in roles.items():
        b[f"{nombre}_kwh"] = kwh[rol] if rol in kwh else np.nan
        b[f"{nombre}_completitud"] = comp[rol] if rol in comp else np.nan
    b["imp_completo"] = b.importacion_completitud >= 1
    b["exp_completo"] = b.exportacion_completitud >= 1
    b["gen_completo"] = b.generacion_fv_completitud >= 1
    b["completitud_minima"] = b[["importacion_completitud", "exportacion_completitud", "generacion_fv_completitud"]].min(axis=1, skipna=False)
    b["balance_completo"] = b.imp_completo & b.exp_completo & b.gen_completo
    b["consumo_planta_kwh"] = (b.importacion_kwh + b.generacion_fv_kwh - b.exportacion_kwh).where(b.balance_completo)
    return b.drop(columns=["importacion_completitud", "exportacion_completitud", "generacion_fv_completitud"]).reset_index()


def tablas_area_equipo(df_ed, areas, equipos):
    cols_d = ["medidor_id", "fecha", "kwh", "kwh_completo", "completitud", "dia_completo"]
    ad = (df_ed[df_ed.elemento_tipo == "area"].rename(columns={"elemento_id": "area_id"})
          .merge(areas[["area_id", "area_nombre", "uso_predominante"]], on="area_id")
          [["planta_id", "area_id", "area_nombre", "uso_predominante"] + cols_d])
    ed = (df_ed[df_ed.elemento_tipo == "equipo"].rename(columns={"elemento_id": "equipo_id"})
          .merge(equipos, on="equipo_id").merge(areas[["area_id", "area_nombre"]], on="area_id")
          [["planta_id", "area_id", "area_nombre", "equipo_id", "equipo_nombre", "categoria_uso",
            "es_monitoreado_prioritario", "potencia_nominal_kw"] + cols_d])
    return ad.reset_index(drop=True), ed.reset_index(drop=True)


def base_dias_areas(df_balance, df_area_diario):
    """[CAMBIO C3] Días comparables para participaciones y no asignado: balance de planta completo
    y TODOS los submedidores de área con 100 % de intervalos válidos ese día."""
    a = df_area_diario.groupby(["planta_id", "fecha"]).agg(
        suma_areas_kwh=("kwh", lambda s: s.sum(min_count=1)),
        areas_completas=("dia_completo", "all"),
        areas_incompletas=("dia_completo", lambda s: int((~s).sum()))).reset_index()
    d = df_balance[["planta_id", "fecha", "consumo_planta_kwh", "balance_completo"]].merge(a, on=["planta_id", "fecha"], how="left")
    d["areas_completas"] = d.areas_completas.fillna(False).astype(bool)
    d["dia_base_areas"] = d.balance_completo & d.areas_completas
    d["no_asignado_kwh"] = (d.consumo_planta_kwh - d.suma_areas_kwh).where(d.dia_base_areas)
    d["motivo_sin_calculo"] = np.select(
        [~d.balance_completo, ~d.areas_completas], ["Balance de planta incompleto", "Submedidor de área incompleto"], "")
    return d


def costo_diario(df_balance, tarifa):
    """Costo energético estimado = importación diaria × costo_promedio_mxn_kwh vigente.
    Sólo en días con importación completa y costo vigente; en otro caso NaN (no 0)."""
    t = tarifa.copy()
    t["vigente_desde"], t["vigente_hasta"] = pd.to_datetime(t.vigente_desde), pd.to_datetime(t.vigente_hasta)
    d = df_balance[["planta_id", "fecha", "importacion_kwh", "imp_completo"]].merge(t, on="planta_id", how="left")
    d = d[(d.fecha >= d.vigente_desde) & (d.fecha <= d.vigente_hasta)]
    d = df_balance[["planta_id", "fecha", "importacion_kwh", "imp_completo"]].merge(
        d[["planta_id", "fecha", "costo_promedio_mxn_kwh", "incluye_iva", "fuente"]], on=["planta_id", "fecha"], how="left")
    d["costo_ok"] = d.imp_completo & d.costo_promedio_mxn_kwh.notna()
    d["costo_estimado_mxn"] = (d.importacion_kwh * d.costo_promedio_mxn_kwh).where(d.costo_ok)
    return d


# =============================================================================
# 5. Periodos y comparaciones
# =============================================================================
def definir_periodos(fecha_inicio, fecha_fin, fecha_min, fecha_max) -> dict:
    """Periodo actual y anterior comparable (misma duración, inmediatamente previo)."""
    ini, fin = pd.Timestamp(fecha_inicio).normalize(), pd.Timestamp(fecha_fin).normalize()
    if fin < ini:
        raise ValueError("La fecha final debe ser igual o posterior a la fecha inicial.")
    if ini < fecha_min or fin > fecha_max:
        raise ValueError(f"El periodo debe estar entre {fecha_min:%d/%m/%Y} y {fecha_max:%d/%m/%Y}.")
    dias = (fin - ini).days + 1
    ant_fin = ini - pd.Timedelta(days=1)
    ant_ini = ant_fin - pd.Timedelta(days=dias - 1)
    return {"actual": (ini, fin), "anterior": (ant_ini, ant_fin), "dias": dias,
            "anterior_disponible": ant_ini >= fecha_min}


def filtrar(df, periodo, col="fecha"):
    ini, fin = periodo
    return df[(df[col] >= ini) & (df[col] <= fin)]


def variacion_pct(actual, anterior):
    """((actual − anterior) / anterior) × 100. None si no es calculable."""
    if actual is None or anterior is None or pd.isna(actual) or pd.isna(anterior) or anterior == 0:
        return None
    return float((actual - anterior) / anterior * 100)


def comparar_periodos(d_act, d_ant, P, col_val="kwh_completo", col_ok="dia_completo") -> dict:
    """[CAMBIO C4] Comparación entre periodos para UNA serie diaria (una fila por día).
    tipo = 'total'            → ambos periodos con todos sus días completos.
    tipo = 'promedio_diario'  → días incompletos, cobertura ≥ COBERTURA_MIN_REF en ambos (referencia).
    tipo = None               → «No disponible»."""
    res = {"tipo": None, "variacion_pct": None, "valor_actual": None, "valor_anterior": None,
           "etiqueta": "No disponible", "nota": ""}
    if not P["anterior_disponible"]:
        res["nota"] = "El periodo anterior comparable queda fuera del rango de datos."
        return res
    n = P["dias"]
    oka, okb = int(d_act[col_ok].sum()), int(d_ant[col_ok].sum())
    if oka == n and okb == n:
        a, b = d_act[col_val].sum(), d_ant[col_val].sum()
        v = variacion_pct(a, b)
        res.update(tipo="total" if v is not None else None, variacion_pct=v, valor_actual=a, valor_anterior=b,
                   etiqueta="Variación del total" if v is not None else "No disponible",
                   nota="" if v is not None else "El periodo anterior tiene valor cero.")
        return res
    if oka > 0 and okb > 0 and oka >= COBERTURA_MIN_REF * n and okb >= COBERTURA_MIN_REF * n:
        a = d_act.loc[d_act[col_ok].astype(bool), col_val].mean()
        b = d_ant.loc[d_ant[col_ok].astype(bool), col_val].mean()
        v = variacion_pct(a, b)
        res.update(tipo="promedio_diario" if v is not None else None, variacion_pct=v, valor_actual=a, valor_anterior=b,
                   etiqueta="Ref. promedio diario" if v is not None else "No disponible",
                   nota=(f"Hay días incompletos ({oka}/{n} días completos en el periodo actual y {okb}/{n} en el anterior). "
                         "Es una referencia basada en el promedio diario de días completos y no equivale a la "
                         "variación del consumo total."))
        return res
    res["nota"] = f"Cobertura insuficiente para comparar ({oka}/{n} y {okb}/{n} días completos)."
    return res


# =============================================================================
# 6. Indicadores por sección
# =============================================================================
def resumen_planta(ctx, P) -> dict:
    b = ctx["df_balance"]
    d, da = filtrar(b, P["actual"]), filtrar(b, P["anterior"])
    base = d[d.balance_completo]
    out = {
        "dias_periodo": P["dias"],
        "consumo_planta_kwh": d.consumo_planta_kwh.sum(min_count=1),
        "dias_balance_completo": int(d.balance_completo.sum()),
        "importacion_kwh": d.loc[d.imp_completo, "importacion_kwh"].sum(min_count=1),
        "dias_importacion": int(d.imp_completo.sum()),
        "exportacion_kwh": d.loc[d.exp_completo, "exportacion_kwh"].sum(min_count=1),
        "dias_exportacion": int(d.exp_completo.sum()),
        "generacion_fv_kwh": d.loc[d.gen_completo, "generacion_fv_kwh"].sum(min_count=1),
        "dias_generacion": int(d.gen_completo.sum()),
        "relacion_generacion_consumo_pct": (base.generacion_fv_kwh.sum() / base.consumo_planta_kwh.sum() * 100
                                            if len(base) and base.consumo_planta_kwh.sum() else None),
    }
    out["promedio_diario_kwh"] = (out["consumo_planta_kwh"] / out["dias_balance_completo"]
                                  if out["dias_balance_completo"] else None)
    out["comparacion_consumo"] = comparar_periodos(d, da, P, "consumo_planta_kwh", "balance_completo")
    return out


def costo_periodo(ctx, P) -> dict:
    c = ctx["df_costo_diario"]
    d, da = filtrar(c, P["actual"]), filtrar(c, P["anterior"])
    tarifas = d.costo_promedio_mxn_kwh.dropna().unique()
    return {"costo_estimado_mxn": d.costo_estimado_mxn.sum(min_count=1),
            "dias_con_costo": int(d.costo_ok.sum()), "dias_periodo": P["dias"],
            "costo_promedio_mxn_kwh": float(tarifas[0]) if len(tarifas) == 1 else None,
            "fuente_costo": ", ".join(map(str, d.fuente.dropna().unique())),
            "comparacion": comparar_periodos(d, da, P, "costo_estimado_mxn", "costo_ok"),
            "nota": "Estimación: energía importada × costo promedio por kWh. No equivale a la factura eléctrica real "
                    "(no incluye demanda, horarios tarifarios, factor de potencia ni otros cargos)."}


def _dias_esperados(idx, frecuencia):
    if frecuencia == "W":
        return pd.Series(7, index=idx)
    if frecuencia == "MS":
        return pd.Series(idx.days_in_month, index=idx)
    return pd.Series(1, index=idx)


def serie_planta(ctx, periodo, frecuencia="D") -> pd.DataFrame:
    """Evolución por día, semana o mes. Sólo suma días completos de cada medidor y señala los
    bloques con días incompletos o con menos días de calendario (extremos del rango)."""
    d = filtrar(ctx["df_balance"], periodo).set_index("fecha")
    x = pd.DataFrame({
        "consumo_planta_kwh": d.consumo_planta_kwh,
        "importacion_kwh": d.importacion_kwh.where(d.imp_completo),
        "exportacion_kwh": d.exportacion_kwh.where(d.exp_completo),
        "generacion_fv_kwh": d.generacion_fv_kwh.where(d.gen_completo),
        "dias_incompletos": (~d.balance_completo).astype(int),
        "dias_en_bloque": 1})
    f = _freq(frecuencia)
    f["rule"] = f.pop("freq")
    s = x[["consumo_planta_kwh", "importacion_kwh", "exportacion_kwh", "generacion_fv_kwh"]].resample(**f).sum(min_count=1)
    s["dias_incompletos"] = x.dias_incompletos.resample(**f).sum()
    s["dias_en_bloque"] = x.dias_en_bloque.resample(**f).sum()
    s["bloque_parcial_calendario"] = s.dias_en_bloque < _dias_esperados(s.index, frecuencia)
    s["advertencia"] = np.where(s.dias_incompletos > 0, "Incluye días con balance incompleto (no sumados)",
                                np.where(s.bloque_parcial_calendario, "Bloque con menos días de calendario", ""))
    return s.reset_index()


def consumo_areas(ctx, P):
    """Consumo por área sobre la base de días comparables [C3], variación por área [C4] y
    consumo no asignado como indicador de balance (puede ser negativo; no se ajusta)."""
    ad, bd = ctx["df_area_diario"], ctx["df_dias_base_areas"]
    d_base = filtrar(bd, P["actual"])
    fechas_ok = d_base.loc[d_base.dia_base_areas, "fecha"]
    consumo_base = d_base.loc[d_base.dia_base_areas, "consumo_planta_kwh"].sum(min_count=1)
    a_act, a_ant = filtrar(ad, P["actual"]), filtrar(ad, P["anterior"])
    filas = []
    for (aid, nom), g in a_act.groupby(["area_id", "area_nombre"]):
        ga = a_ant[a_ant.area_id == aid]
        comp = comparar_periodos(g, ga, P)
        kwh_base = g.loc[g.fecha.isin(fechas_ok), "kwh"].sum(min_count=1)
        filas.append({
            "area_id": aid, "area_nombre": nom, "uso_predominante": g.uso_predominante.iloc[0],
            "kwh_base": kwh_base,
            "participacion_pct": kwh_base / consumo_base * 100 if pd.notna(consumo_base) and consumo_base else None,
            "kwh_dias_completos": g.kwh_completo.sum(min_count=1),
            "dias_completos": int(g.dia_completo.sum()),
            "dias_parciales": int(((g.completitud > 0) & (g.completitud < 1)).sum()),
            "dias_sin_datos": int((g.completitud == 0).sum()),
            "variacion_pct": comp["variacion_pct"], "variacion_tipo": comp["tipo"],
            "variacion_etiqueta": comp["etiqueta"], "variacion_nota": comp["nota"],
            "kwh_anterior": comp["valor_anterior"] if comp["tipo"] == "total" else None})
    df = pd.DataFrame(filas)
    if df.empty:
        return df, {"dias_base": 0, "dias_periodo": P["dias"], "consumo_base_kwh": None, "no_asignado_kwh": None,
                    "no_asignado_pct": None, "residual_negativo": False, "dias_excluidos": pd.DataFrame()}
    df["ranking"] = df.kwh_base.rank(ascending=False, method="min")
    df = df.sort_values("kwh_base", ascending=False).reset_index(drop=True)
    suma_areas = df.kwh_base.sum(min_count=1)
    no_asig = consumo_base - suma_areas if pd.notna(consumo_base) and pd.notna(suma_areas) else None
    excl = d_base.loc[~d_base.dia_base_areas, ["fecha", "motivo_sin_calculo"]]
    meta = {"dias_base": int(d_base.dia_base_areas.sum()), "dias_periodo": P["dias"],
            "consumo_base_kwh": consumo_base, "suma_areas_kwh": suma_areas,
            "no_asignado_kwh": no_asig,
            "no_asignado_pct": no_asig / consumo_base * 100 if no_asig is not None and consumo_base else None,
            "residual_negativo": no_asig is not None and no_asig < 0,
            "dias_excluidos": excl}
    return df, meta


def area_mayor_incremento(df_areas, minimo_kwh):
    """Sólo aumentos, con variación del total (no con la referencia de promedio diario) y base ≥ minimo_kwh."""
    if df_areas.empty:
        return None
    c = df_areas[(df_areas.variacion_tipo == "total") & (df_areas.kwh_anterior >= minimo_kwh) & (df_areas.variacion_pct > 0)]
    if c.empty:
        return None
    return c.sort_values("variacion_pct", ascending=False).iloc[0][["area_id", "area_nombre", "variacion_pct"]].to_dict()


def serie_elementos(df_diario, periodo, claves, frecuencia="D"):
    """Serie de consumo por área o equipo usando sólo días completos (los parciales quedan vacíos)."""
    d = filtrar(df_diario, periodo)
    g = d.groupby(claves + [pd.Grouper(key="fecha", **_freq(frecuencia))])
    s = g.kwh_completo.sum(min_count=1).rename("kwh").to_frame()
    s["dias_incompletos"] = g.dia_completo.apply(lambda x: int((~x).sum()))
    return s.reset_index()


def conteo_equipos(ctx):
    equipos, med = ctx["base"]["equipo"], ctx["base"]["medidor"]
    medidos = set(med.loc[med.elemento_tipo == "equipo", "elemento_id"])
    prio = equipos[equipos.es_monitoreado_prioritario == 1]
    return {"equipos_registrados": len(equipos), "equipos_con_medicion": len(medidos),
            "equipos_prioritarios": len(prio), "prioritarios_con_medicion": int(prio.equipo_id.isin(medidos).sum()),
            "prioritarios_sin_medicion": prio.loc[~prio.equipo_id.isin(medidos), "equipo_nombre"].tolist(),
            "equipos_sin_medicion": equipos.loc[~equipos.equipo_id.isin(medidos), "equipo_nombre"].tolist()}


def consumo_equipos(ctx, P, solo_prioritarios=True, categoria=None) -> pd.DataFrame:
    """Consumo por equipo con medición propia (días completos), variación [C4] y participación
    dentro de su área calculada sólo en días donde el equipo y su área están completos."""
    ed, ad = ctx["df_equipo_diario"], ctx["df_area_diario"]
    if solo_prioritarios:
        ed = ed[ed.es_monitoreado_prioritario == 1]
    if categoria:
        ed = ed[ed.categoria_uso == categoria]
    e_act, e_ant, a_act = filtrar(ed, P["actual"]), filtrar(ed, P["anterior"]), filtrar(ad, P["actual"])
    filas = []
    for (eid, nom), g in e_act.groupby(["equipo_id", "equipo_nombre"]):
        comp = comparar_periodos(g, e_ant[e_ant.equipo_id == eid], P)
        area_id = g.area_id.iloc[0]
        m = g[["fecha", "kwh", "dia_completo"]].merge(
            a_act.loc[a_act.area_id == area_id, ["fecha", "kwh", "dia_completo"]], on="fecha", suffixes=("_eq", "_area"))
        m = m[m.dia_completo_eq & m.dia_completo_area]
        part = m.kwh_eq.sum() / m.kwh_area.sum() * 100 if len(m) and m.kwh_area.sum() else None
        dias_ok = int(g.dia_completo.sum())
        kwh = g.kwh_completo.sum(min_count=1)
        filas.append({
            "equipo_id": eid, "equipo_nombre": nom, "area_id": area_id, "area_nombre": g.area_nombre.iloc[0],
            "categoria_uso": g.categoria_uso.iloc[0], "es_monitoreado_prioritario": int(g.es_monitoreado_prioritario.iloc[0]),
            "potencia_nominal_kw": g.potencia_nominal_kw.iloc[0],
            "kwh_dias_completos": kwh, "dias_completos": dias_ok, "dias_incompletos": int(len(g) - dias_ok),
            "promedio_diario_kwh": kwh / dias_ok if dias_ok else None,
            "participacion_en_area_pct": part,
            "variacion_pct": comp["variacion_pct"], "variacion_tipo": comp["tipo"],
            "variacion_etiqueta": comp["etiqueta"], "variacion_nota": comp["nota"],
            "kwh_anterior": comp["valor_anterior"] if comp["tipo"] == "total" else None})
    df = pd.DataFrame(filas)
    if df.empty:
        return df
    df["ranking"] = df.kwh_dias_completos.rank(ascending=False, method="min")
    return df.sort_values("ranking").reset_index(drop=True)


def equipo_mayor_incremento(df_eq, minimo_kwh):
    if df_eq.empty:
        return None
    c = df_eq[(df_eq.variacion_tipo == "total") & (df_eq.kwh_anterior >= minimo_kwh) & (df_eq.variacion_pct > 0)]
    if c.empty:
        return None
    return c.sort_values("variacion_pct", ascending=False).iloc[0][["equipo_id", "equipo_nombre", "variacion_pct"]].to_dict()


def solar(ctx, P) -> dict:
    """Indicadores FV. Se distingue: días con generación registrada (incluye ceros medidos) vs.
    días sin lecturas del inversor (no se suman ni se interpretan como cero)."""
    b = ctx["df_balance"]
    d, da = filtrar(b, P["actual"]), filtrar(b, P["anterior"])
    kwp = float(ctx["base"]["sistema_fv"].capacidad_kwp.sum())
    gen = d.loc[d.gen_completo, "generacion_fv_kwh"].sum(min_count=1)
    dias_gen = int(d.gen_completo.sum())
    base = d[d.balance_completo]
    gen_b, exp_b, cons_b = base.generacion_fv_kwh.sum(), base.exportacion_kwh.sum(), base.consumo_planta_kwh.sum()
    return {
        "capacidad_kwp": kwp, "dias_periodo": P["dias"],
        "generacion_fv_kwh": gen, "dias_generacion_registrada": dias_gen,
        "dias_sin_lecturas_fv": int((d.generacion_fv_kwh.isna()).sum()),
        "dias_parciales_fv": int((~d.gen_completo & d.generacion_fv_kwh.notna()).sum()),
        "dias_cero_medido": int((d.gen_completo & (d.generacion_fv_kwh == 0)).sum()),
        "promedio_diario_kwh": gen / dias_gen if dias_gen else None,
        "rendimiento_especifico_kwh_kwp": gen / kwp if kwp and pd.notna(gen) else None,
        "comparacion": comparar_periodos(d, da, P, "generacion_fv_kwh", "gen_completo"),
        "dias_base_balance": len(base),
        "relacion_generacion_consumo_pct": gen_b / cons_b * 100 if len(base) and cons_b else None,
        "fv_autoconsumida_kwh": gen_b - exp_b if len(base) else None,
        "fraccion_autoconsumida_pct": (gen_b - exp_b) / gen_b * 100 if len(base) and gen_b else None,
        "exportacion_base_kwh": exp_b if len(base) else None}


def solar_diario(ctx, periodo) -> pd.DataFrame:
    d = filtrar(ctx["df_balance"], periodo).copy()
    d["estado_fv"] = np.select(
        [d.generacion_fv_kwh.isna(), ~d.gen_completo, d.generacion_fv_kwh == 0],
        ["Sin lecturas", "Parcial", "Cero medido"], "Registrada")
    d["generacion_fv_kwh_completa"] = d.generacion_fv_kwh.where(d.gen_completo)
    return d[["fecha", "generacion_fv_kwh_completa", "estado_fv", "exportacion_kwh", "consumo_planta_kwh",
              "importacion_kwh", "balance_completo"]]


def climatizacion(ctx, P):
    """Área HVAC (uso_predominante = 'climatizacion'). Participación sobre días con balance de planta
    completo y medidor HVAC completo [C3]. No se calcula distribución por zonas (nota N1)."""
    ad, b = ctx["df_area_diario"], ctx["df_balance"]
    hv = ad[ad.uso_predominante == "climatizacion"]
    diario = hv.groupby("fecha").agg(kwh=("kwh", lambda s: s.sum(min_count=1)), dia_completo=("dia_completo", "all")).reset_index()
    diario["kwh_completo"] = diario.kwh.where(diario.dia_completo)
    diario = diario.merge(b[["fecha", "consumo_planta_kwh", "balance_completo"]], on="fecha", how="left")
    d, da = filtrar(diario, P["actual"]), filtrar(diario, P["anterior"])
    base = d[d.dia_completo & d.balance_completo]
    eqs = consumo_equipos(ctx, P, solo_prioritarios=False, categoria="climatizacion")
    kpi = {"consumo_climatizacion_kwh": d.kwh_completo.sum(min_count=1),
           "dias_completos": int(d.dia_completo.sum()), "dias_periodo": P["dias"],
           "promedio_diario_kwh": d.kwh_completo.mean() if d.dia_completo.any() else None,
           "participacion_planta_pct": base.kwh.sum() / base.consumo_planta_kwh.sum() * 100 if len(base) else None,
           "dias_base_participacion": len(base),
           "comparacion": comparar_periodos(d, da, P),
           "equipo_hvac_mayor_consumo": eqs.iloc[0].equipo_nombre if len(eqs) else None,
           "consumo_equipos_hvac_medidos_kwh": eqs.kwh_dias_completos.sum() if len(eqs) else None,
           "areas_hvac": hv.area_nombre.unique().tolist()}
    s_act = d[["fecha", "kwh_completo"]].assign(periodo="Periodo actual")
    s_act["dia_relativo"] = (s_act.fecha - P["actual"][0]).dt.days + 1
    if P["anterior_disponible"]:
        s_ant = da[["fecha", "kwh_completo"]].assign(periodo="Periodo anterior")
        s_ant["dia_relativo"] = (s_ant.fecha - P["anterior"][0]).dt.days + 1
        serie = pd.concat([s_act, s_ant], ignore_index=True)
    else:
        serie = s_act
    return kpi, eqs, serie


# =============================================================================
# 7. Alertas (evaluadas sobre el histórico) — lógica de reglas del notebook
# =============================================================================
def evaluar_reglas(ctx):
    reglas = ctx["base"]["regla_alerta"].set_index("regla_id")
    med = ctx["base"]["medidor"]
    df_ed, df_balance = ctx["df_energia_diaria"], ctx["df_balance"]
    df_area, df_eq, df_base = ctx["df_area_diario"], ctx["df_equipo_diario"], ctx["df_dias_base_areas"]
    fmin, fmax = ctx["fecha_min"], ctx["fecha_max"]
    ts = ctx["momento_procesamiento"]
    comp_min = float(reglas.loc["R5", "umbral_atencion"]) if "R5" in reglas.index else 0.9
    no_evaluados = []

    def nueva_alerta(regla_id, fecha_eval, el_tipo, el_id, prioridad, valor, mensaje, nota_cobertura=""):
        return {"alerta_id": f"ALR_{regla_id}_{el_id}_{pd.Timestamp(fecha_eval):%Y%m%d}", "regla_id": regla_id,
                "timestamp_deteccion": ts, "fecha_evaluada": pd.Timestamp(fecha_eval).normalize(),
                "elemento_tipo": el_tipo, "elemento_id": el_id, "prioridad": prioridad,
                "valor_detectado": round(float(valor), 4), "mensaje": mensaje, "estado": ESTADO_ALERTA,
                "nota_cobertura": nota_cobertura}

    def bloques(ventana):
        out, fin = [], fmax
        while fin - pd.Timedelta(days=2 * ventana - 1) >= fmin:
            a = (fin - pd.Timedelta(days=ventana - 1), fin)
            b = (a[0] - pd.Timedelta(days=ventana), a[0] - pd.Timedelta(days=1))
            out.append((a, b))
            fin = a[0] - pd.Timedelta(days=1)
        return out

    def series_elementos():
        pl = df_balance.assign(elemento_tipo="planta", elemento_id=df_balance.planta_id, nombre="Planta",
                               kwh=df_balance.consumo_planta_kwh, completitud=df_balance.completitud_minima)
        ar = df_area.assign(elemento_tipo="area", elemento_id=df_area.area_id, nombre=df_area.area_nombre)
        eqd = df_eq.assign(elemento_tipo="equipo", elemento_id=df_eq.equipo_id, nombre=df_eq.equipo_nombre)
        cols = ["elemento_tipo", "elemento_id", "nombre", "fecha", "kwh", "completitud"]
        return pd.concat([pl[cols], ar[cols], eqd[cols]], ignore_index=True)

    def nota_cob(ga, gb):
        n = int((ga.completitud < 1).sum() + (gb.completitud < 1).sum())
        return (f"{n} día(s) de los bloques comparados tienen entre {comp_min*100:.0f} % y 100 % de intervalos válidos."
                if n else "")

    def regla_variacion_consumo(rid, sentido):
        r = reglas.loc[rid]; v = int(r.ventana_dias); s = series_elementos(); out = []
        for (a, b) in bloques(v):
            for (tipo, eid, nom), g in s.groupby(["elemento_tipo", "elemento_id", "nombre"]):
                ga, gb = filtrar(g, a), filtrar(g, b)
                if ga.completitud.min() < comp_min or gb.completitud.min() < comp_min or ga.kwh.isna().any() or gb.kwh.isna().any():
                    no_evaluados.append({"regla_id": rid, "elemento_id": eid, "fecha_evaluada": a[1], "motivo": "Completitud insuficiente"})
                    continue
                ka, kb = ga.kwh.sum(), gb.kwh.sum()
                if kb < r.minimo_kwh:
                    continue
                var = (ka - kb) / kb
                per = f"{a[0]:%d/%m}–{a[1]:%d/%m} vs. {b[0]:%d/%m}–{b[1]:%d/%m}"
                if sentido == "aumento" and var >= r.umbral_atencion:
                    pr = "alta" if pd.notna(r.umbral_alta) and var >= r.umbral_alta else "atencion"
                    out.append(nueva_alerta(rid, a[1], tipo, eid, pr, var,
                               f"Aumento de consumo de {var*100:.1f} % en {nom} ({per}). Revisar comportamiento.", nota_cob(ga, gb)))
                if sentido == "disminucion" and var <= -r.umbral_atencion:
                    out.append(nueva_alerta(rid, a[1], tipo, eid, "informativa", var,
                               f"Disminución de consumo de {abs(var)*100:.1f} % en {nom} ({per}). Confirmar si corresponde a un cambio operativo.",
                               nota_cob(ga, gb)))
        return out

    def regla_variacion_generacion(rid):
        r = reglas.loc[rid]; v = int(r.ventana_dias); out = []
        g = df_ed[df_ed.tipo_medicion == "generacion_fv"]
        for (a, b) in bloques(v):
            for (mid, sid), x in g.groupby(["medidor_id", "elemento_id"]):
                xa, xb = filtrar(x, a), filtrar(x, b)
                if xa.completitud.min() < comp_min or xb.completitud.min() < comp_min:
                    no_evaluados.append({"regla_id": rid, "elemento_id": sid, "fecha_evaluada": a[1], "motivo": "Completitud insuficiente"})
                    continue
                if xb.kwh.sum() == 0:
                    continue
                var = (xa.kwh.sum() - xb.kwh.sum()) / xb.kwh.sum()
                if var <= -r.umbral_atencion:
                    out.append(nueva_alerta(rid, a[1], "sistema_fv", sid, "atencion", var,
                               f"Disminución de generación FV de {abs(var)*100:.1f} % ({a[0]:%d/%m}–{a[1]:%d/%m} vs. bloque anterior). "
                               "Requiere análisis; puede deberse a condiciones climáticas.", nota_cob(xa, xb)))
        return out

    def regla_generacion_minima(rid):
        r = reglas.loc[rid]; n = 7; out = []   # 7 días previos, según la descripción de la regla
        g = df_ed[df_ed.tipo_medicion == "generacion_fv"].sort_values("fecha")
        for (mid, sid), x in g.groupby(["medidor_id", "elemento_id"]):
            x = x.set_index("fecha")
            valida = x.kwh.where(x.completitud >= comp_min)
            ref = valida.shift(1).rolling(n, min_periods=n).mean()
            for f, val in valida.items():
                if pd.isna(val):
                    continue
                if pd.isna(ref[f]):
                    if f >= fmin + pd.Timedelta(days=n):   # fuera del arranque natural de la serie
                        no_evaluados.append({"regla_id": rid, "elemento_id": sid, "fecha_evaluada": f,
                                             "motivo": "Sin 7 días previos con datos suficientes"})
                    continue
                if ref[f] == 0:
                    continue
                if val < r.umbral_alta * ref[f]:
                    out.append(nueva_alerta(rid, f, "sistema_fv", sid, "alta", val / ref[f],
                               f"La generación FV del {f:%d/%m/%Y} fue {val:.1f} kWh ({val/ref[f]*100:.1f} % del promedio "
                               "de los 7 días previos). Revisar inversor y comunicación."))
        return out

    def regla_completitud(rid):
        r = reglas.loc[rid]
        d = df_ed[df_ed.completitud < r.umbral_atencion]
        return [nueva_alerta(rid, x.fecha, "medidor", x.medidor_id, "atencion", x.completitud,
                f"El medidor {x.medidor_id} tiene {x.completitud*100:.0f} % de datos válidos el {x.fecha:%d/%m/%Y} "
                f"({x.filas_recibidas} filas recibidas, {x.intervalos_no_validos} no válidas). Los indicadores de ese día pueden estar incompletos.")
                for x in d.itertuples()]

    def regla_residual(rid):
        r = reglas.loc[rid]; out = []
        for x in df_base.itertuples():
            if not x.dia_base_areas:
                if x.balance_completo:
                    no_evaluados.append({"regla_id": rid, "elemento_id": x.planta_id, "fecha_evaluada": x.fecha,
                                         "motivo": x.motivo_sin_calculo})
                continue
            res = x.no_asignado_kwh / x.consumo_planta_kwh
            if res < -r.umbral_atencion:
                out.append(nueva_alerta(rid, x.fecha, "planta", x.planta_id, "atencion", res,
                           f"La suma de submedidores de área supera el consumo de planta el {x.fecha:%d/%m/%Y}. Revisar configuración o calibración."))
        hijos = med[med.medidor_padre_id.notna()].groupby("medidor_padre_id").medidor_id.agg(list)
        for padre, hs in hijos.items():
            p = df_ed[df_ed.medidor_id == padre].set_index("fecha")
            h = df_ed[df_ed.medidor_id.isin(hs)].pivot_table(index="fecha", columns="medidor_id", values="kwh")
            hc = df_ed[df_ed.medidor_id.isin(hs)].groupby("fecha").completitud.min()
            for f in p.index:
                if p.completitud[f] < 1 or hc.get(f, 0) < 1:
                    continue
                res = (p.kwh[f] - h.loc[f].sum()) / p.kwh[f]
                if res < -r.umbral_atencion:
                    out.append(nueva_alerta(rid, f, "area", p.elemento_id[f], "atencion", res,
                               f"La suma de equipos supera al medidor {padre} el {f:%d/%m/%Y}. Revisar configuración del árbol de medición."))
        return out

    implementacion = {
        "R1": (lambda: regla_variacion_consumo("R1", "aumento"), "Implementada", ""),
        "R2": (lambda: regla_variacion_consumo("R2", "disminucion"), "Implementada", "umbral_alta vacío → prioridad 'informativa'."),
        "R3": (lambda: regla_variacion_generacion("R3"), "Implementada", ""),
        "R4": (lambda: regla_generacion_minima("R4"), "Implementada", "7 días de referencia tomados de la descripción de la regla."),
        "R5": (lambda: regla_completitud("R5"), "Implementada", "Incluye días sin filas (huecos)."),
        "R6": (lambda: regla_residual("R6"), "Implementada", "Nivel planta: sólo días con balance y todas las áreas completas."),
        "R7": (None, "Oportunidad (se evalúa por periodo)", "Tipo 'oportunidad', no alerta."),
    }
    alertas, estado = [], []
    for rid, r in reglas.iterrows():
        fn, est, nota = implementacion.get(rid, (None, "Requiere definición adicional", "Tipo de regla no reconocido."))
        if r.activa != 1:
            estado.append({"regla_id": rid, "tipo_regla": r.tipo_regla, "descripcion": r.descripcion,
                           "estado": "Inactiva", "alertas_historicas": 0, "nota": ""})
            continue
        res = fn() if fn else []
        alertas += res
        estado.append({"regla_id": rid, "tipo_regla": r.tipo_regla, "descripcion": r.descripcion,
                       "estado": est, "alertas_historicas": len(res), "nota": nota})
    cols = list(ctx["base"]["alerta_generada"].columns) + ["nota_cobertura"]
    df_al = (pd.DataFrame(alertas, columns=cols).drop_duplicates("alerta_id")
             .sort_values(["fecha_evaluada", "regla_id"]).reset_index(drop=True))
    return df_al, pd.DataFrame(estado), pd.DataFrame(no_evaluados, columns=["regla_id", "elemento_id", "fecha_evaluada", "motivo"])


def nombre_elemento(ctx, tipo, eid):
    b = ctx["base"]
    mapas = {"planta": b["planta"].set_index("planta_id").planta_nombre,
             "area": b["area"].set_index("area_id").area_nombre,
             "equipo": b["equipo"].set_index("equipo_id").equipo_nombre,
             "sistema_fv": pd.Series({s: f"Sistema FV {s}" for s in b["sistema_fv"].sistema_fv_id}),
             "medidor": b["medidor"].set_index("medidor_id").descripcion}
    return mapas.get(tipo, pd.Series(dtype=str)).get(eid, eid)


def area_de_alerta(ctx, tipo, eid):
    b = ctx["base"]
    eq_area = b["equipo"].set_index("equipo_id").area_id
    if tipo == "area":
        return eid
    if tipo == "equipo":
        return eq_area.get(eid)
    if tipo == "medidor":
        m = b["medidor"].set_index("medidor_id").loc[eid]
        return m.elemento_id if m.elemento_tipo == "area" else (eq_area.get(m.elemento_id) if m.elemento_tipo == "equipo" else None)
    return None


def alertas_periodo(ctx, P) -> tuple[pd.DataFrame, dict]:
    """[CAMBIO C6] Sólo condiciones detectadas en el periodo (por fecha_evaluada)."""
    al = filtrar(ctx["df_alertas"], P["actual"], col="fecha_evaluada").copy()
    areas = ctx["base"]["area"].set_index("area_id").area_nombre
    reglas = ctx["base"]["regla_alerta"].set_index("regla_id").descripcion
    if len(al):
        al["elemento"] = [nombre_elemento(ctx, t, e) for t, e in zip(al.elemento_tipo, al.elemento_id)]
        al["area"] = [areas.get(area_de_alerta(ctx, t, e)) for t, e in zip(al.elemento_tipo, al.elemento_id)]
        al["regla"] = al.regla_id.map(reglas)
    else:
        al = al.assign(elemento=None, area=None, regla=None)
    top = al.area.dropna().value_counts()
    return al, {"alertas_historicas_periodo": len(al),
                "prioridad_alta": int((al.prioridad == "alta").sum()),
                "prioridad_atencion": int((al.prioridad == "atencion").sum()),
                "informativas": int((al.prioridad == "informativa").sum()),
                "area_con_mas_alertas": top.index[0] if len(top) else None}


def oportunidades(ctx, df_areas, df_equipos, P) -> pd.DataFrame:
    """Regla R7 sobre el periodo. Conservador: sólo con variación del total (no con la referencia de
    promedio diario). Umbral de aumento = umbral_atencion de R1 (10 %, igual a la descripción de R7).
    Participación de áreas sobre la base de días comparables; de equipos, sobre su área."""
    reglas = ctx["base"]["regla_alerta"].set_index("regla_id")
    cols = ["elemento_tipo", "elemento_id", "elemento", "participacion_pct", "base_participacion", "variacion_pct", "regla_id", "observacion"]
    if "R7" not in reglas.index or reglas.loc["R7", "activa"] != 1:
        return pd.DataFrame(columns=cols)
    r = reglas.loc["R7"]
    u_part, u_var = r.umbral_atencion * 100, reglas.loc["R1", "umbral_atencion"] * 100
    out = []
    for x in df_areas.itertuples():
        if (x.variacion_tipo == "total" and pd.notna(x.participacion_pct) and x.participacion_pct >= u_part
                and x.variacion_pct >= u_var and x.kwh_anterior >= r.minimo_kwh):
            out.append({"elemento_tipo": "area", "elemento_id": x.area_id, "elemento": x.area_nombre,
                        "participacion_pct": x.participacion_pct, "base_participacion": "Consumo de planta (días comparables)",
                        "variacion_pct": x.variacion_pct, "regla_id": "R7",
                        "observacion": f"{x.area_nombre} representa {x.participacion_pct:.1f} % del consumo de planta y aumentó "
                                       f"{x.variacion_pct:.1f} % respecto al periodo anterior. Posible oportunidad de revisión."})
    for x in df_equipos.itertuples():
        if (x.variacion_tipo == "total" and pd.notna(x.participacion_en_area_pct) and x.participacion_en_area_pct >= u_part
                and x.variacion_pct >= u_var and x.kwh_anterior >= r.minimo_kwh):
            out.append({"elemento_tipo": "equipo", "elemento_id": x.equipo_id, "elemento": x.equipo_nombre,
                        "participacion_pct": x.participacion_en_area_pct, "base_participacion": f"Consumo del área {x.area_nombre}",
                        "variacion_pct": x.variacion_pct, "regla_id": "R7",
                        "observacion": f"{x.equipo_nombre} representa {x.participacion_en_area_pct:.1f} % del consumo de "
                                       f"{x.area_nombre} y aumentó {x.variacion_pct:.1f} %. Requiere análisis técnico."})
    return pd.DataFrame(out, columns=cols)


# =============================================================================
# 8. Calidad de datos del periodo
# =============================================================================
def calidad_periodo(ctx, P) -> dict:
    ed = filtrar(ctx["df_energia_diaria"], P["actual"])
    desc = ctx["base"]["medidor"].set_index("medidor_id").descripcion
    inc = ed[ed.completitud < 1].copy()
    inc["situacion"] = np.where(inc.completitud == 0, "Sin lecturas válidas", "Día parcial")
    inc["descripcion"] = inc.medidor_id.map(desc)
    lec = ctx["lec_rol"]
    lp = lec[(lec.fecha >= P["actual"][0]) & (lec.fecha <= P["actual"][1])]
    esp = ed.intervalos_esperados.sum()
    return {"incidencias": inc[["fecha", "medidor_id", "descripcion", "situacion", "completitud", "filas_recibidas",
                                "intervalos_no_validos", "kwh"]].sort_values(["fecha", "medidor_id"]).reset_index(drop=True),
            "lecturas_excluidas": lp.loc[~lp.es_valida, ["medidor_id", "timestamp_inicio", "energia_intervalo_kwh", "calidad_dato"]],
            "lecturas_estimadas": lp.loc[lp.calidad_dato == "ESTIMADO", ["medidor_id", "timestamp_inicio", "energia_intervalo_kwh"]],
            "completitud_pct": ed.intervalos_validos.sum() / esp * 100 if esp else None,
            "hay_incidencias": len(inc) > 0}


# =============================================================================
# 9. Validación contra tablas calc_ (sólo referencia)
# =============================================================================
def validar_contra_calc(ctx) -> tuple[pd.DataFrame, dict]:
    calc = {h: d.copy() for h, d in ctx["calc"].items()}
    for d in calc.values():
        if "fecha" in d:
            d["fecha"] = pd.to_datetime(d["fecha"])

    def comparar(nombre, py, ref, llaves, col):
        m = py[llaves + [col]].merge(ref[llaves + [col]], on=llaves, how="outer", suffixes=("_py", "_calc"), indicator=True)
        a, b = f"{col}_py", f"{col}_calc"
        m[a], m[b] = m[a].astype(float), m[b].astype(float)
        m["dif_abs"] = (m[a] - m[b]).abs()
        m["coincide"] = (m.dif_abs <= TOL_ABS) | (m[a].isna() & m[b].isna())
        fila = {"indicador": nombre, "resultado_python": m[a].sum(), "resultado_calc": m[b].sum(),
                "registros_comparados": int((m._merge == "both").sum()), "registros_distintos": int((~m.coincide).sum()),
                "estado": "COINCIDE" if (~m.coincide).sum() == 0 else "REVISAR"}
        return fila, m.loc[~m.coincide, llaves + [a, b]]

    na = ctx["df_dias_base_areas"]
    pruebas = [
        ("Energía diaria por medidor (kWh)", ctx["df_energia_diaria"], "calc_energia_diaria_medidor", ["medidor_id", "fecha"], "kwh"),
        ("Completitud diaria por medidor", ctx["df_energia_diaria"], "calc_energia_diaria_medidor", ["medidor_id", "fecha"], "completitud"),
        ("Importación de red (kWh)", ctx["df_balance"], "calc_balance_planta_diario", ["planta_id", "fecha"], "importacion_kwh"),
        ("Exportación a red (kWh)", ctx["df_balance"], "calc_balance_planta_diario", ["planta_id", "fecha"], "exportacion_kwh"),
        ("Generación FV (kWh)", ctx["df_balance"], "calc_balance_planta_diario", ["planta_id", "fecha"], "generacion_fv_kwh"),
        ("Consumo de planta (kWh)", ctx["df_balance"], "calc_balance_planta_diario", ["planta_id", "fecha"], "consumo_planta_kwh"),
        ("Consumo por área (kWh)", ctx["df_area_diario"], "calc_consumo_area_diario", ["area_id", "fecha"], "kwh"),
        ("Consumo por equipo (kWh)", ctx["df_equipo_diario"], "calc_consumo_equipo_diario", ["equipo_id", "fecha"], "kwh"),
        ("Consumo no asignado (kWh)", na, "calc_no_asignado_diario", ["planta_id", "fecha"], "no_asignado_kwh"),
        ("Costo energético estimado (MXN)", ctx["df_costo_diario"], "calc_costo_estimado_diario", ["planta_id", "fecha"], "costo_estimado_mxn"),
    ]
    filas, difs = [], {}
    for nombre, py, hoja, k, c in pruebas:
        if hoja not in calc:
            continue
        f, d = comparar(nombre, py, calc[hoja], k, c)
        filas.append(f)
        if len(d):
            difs[nombre] = d
    return pd.DataFrame(filas), difs


NOTAS_DIFERENCIAS_CALC = [
    "14/08/2026 — Generación FV y consumo de planta: no existe ninguna lectura del inversor (MED_003). "
    "La tabla calc_ registra generación 0 y calcula consumo = importación − exportación (3,264.9 kWh), lo que subestima el consumo. "
    "El pipeline deja la generación y el consumo de ese día sin calcular (ausencia de información, no cero).",
    "14/08/2026 — Completitud de MED_003: el pipeline la reporta como 0 % (día incluido en la malla completa); calc_ no tiene ese registro.",
    "14/08/2026 y 20/08/2026 — Consumo no asignado: el pipeline sólo lo calcula en días con balance completo y todas las áreas completas. "
    "El 14/08 falta la generación FV y el 20/08 el submedidor de Oficinas (MED_013) tiene 6 horas faltantes. "
    "calc_ informa −811.8 kWh y 209.9 kWh respectivamente, valores afectados por esos huecos.",
]


# =============================================================================
# 10. Procesamiento completo y punto de entrada para Streamlit
# =============================================================================
def procesar_base(hojas: dict[str, pd.DataFrame]) -> dict:
    """Procesa la base una sola vez (independiente del periodo). Devuelve el contexto «ctx»."""
    base = {t: hojas[t].copy() for t in TABLAS_BASE}
    calc = {h: d.copy() for h, d in hojas.items() if h.startswith("calc_")}
    med = base["medidor"]
    lec = base["lectura"].copy()
    lec["timestamp_inicio"] = pd.to_datetime(lec["timestamp_inicio"])
    validacion, huecos = validar_base(base, lec)
    lec_rol = preparar_lecturas(lec, med)
    df_ed = energia_diaria_medidor(lec_rol, med)
    df_balance = balance_planta_diario(df_ed)
    df_area, df_eq = tablas_area_equipo(df_ed, base["area"], base["equipo"])
    ctx = {
        "base": base, "calc": calc, "lec_rol": lec_rol, "validacion_inicial": validacion, "huecos": huecos,
        "df_energia_diaria": df_ed, "df_balance": df_balance,
        "df_area_diario": df_area, "df_equipo_diario": df_eq,
        "df_dias_base_areas": base_dias_areas(df_balance, df_area),
        "df_costo_diario": costo_diario(df_balance, base["tarifa_costo"]),
        "fecha_min": df_ed.fecha.min(), "fecha_max": df_ed.fecha.max(),
        "momento_procesamiento": pd.Timestamp.now().floor("s"),
        "minimo_kwh": float(base["regla_alerta"].set_index("regla_id").loc["R1", "minimo_kwh"]),
    }
    ctx["df_alertas"], ctx["df_estado_reglas"], ctx["df_no_evaluados"] = evaluar_reglas(ctx)
    ctx["df_validacion_calc"], ctx["diferencias_calc"] = validar_contra_calc(ctx) if calc else (pd.DataFrame(), {})
    return ctx


def calcular_indicadores(ctx, fecha_inicio, fecha_fin, frecuencia="D") -> dict:
    """Punto de entrada que llama Streamlit con el rango elegido por el usuario."""
    P = definir_periodos(fecha_inicio, fecha_fin, ctx["fecha_min"], ctx["fecha_max"])
    areas_df, areas_meta = consumo_areas(ctx, P)
    eq_prio = consumo_equipos(ctx, P, solo_prioritarios=True)
    eq_all = consumo_equipos(ctx, P, solo_prioritarios=False)
    hv, hv_eq, hv_serie = climatizacion(ctx, P)
    al, al_res = alertas_periodo(ctx, P)
    return {
        "periodos": P,
        "resumen": resumen_planta(ctx, P),
        "costo": costo_periodo(ctx, P),
        "serie_planta": serie_planta(ctx, P["actual"], frecuencia),
        "areas": areas_df, "areas_meta": areas_meta,
        "areas_serie": serie_elementos(ctx["df_area_diario"], P["actual"], ["area_id", "area_nombre"], frecuencia),
        "area_mayor_incremento": area_mayor_incremento(areas_df, ctx["minimo_kwh"]),
        "equipos_prioritarios": eq_prio, "equipos_medidos": eq_all,
        "equipos_serie": serie_elementos(ctx["df_equipo_diario"], P["actual"], ["equipo_id", "equipo_nombre"], frecuencia),
        "equipo_mayor_incremento": equipo_mayor_incremento(eq_all, ctx["minimo_kwh"]),
        "conteo_equipos": conteo_equipos(ctx),
        "solar": solar(ctx, P), "solar_diario": solar_diario(ctx, P["actual"]),
        "hvac": hv, "hvac_equipos": hv_eq, "hvac_serie": hv_serie,
        "alertas": al, "alertas_resumen": al_res,
        "oportunidades": oportunidades(ctx, areas_df, eq_all, P),
        "calidad": calidad_periodo(ctx, P),
    }
