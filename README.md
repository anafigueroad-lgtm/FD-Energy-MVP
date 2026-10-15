# FD Energy — Dashboard MVP (Streamlit)

Primera versión del dashboard del MVP académico de FD Energy. Procesa la base de datos histórica de
demostración y presenta seis secciones: Resumen general, Consumo por áreas, Equipos críticos,
Energía solar, Climatización, y Alertas y oportunidades.

> Es un MVP académico en desarrollo. Usa datos históricos de demostración, no recibe datos en tiempo
> real, no está validado con clientes y sus observaciones no sustituyen un diagnóstico técnico.

## Estructura

```
fd_energy_mvp/
├── app.py               Interfaz Streamlit (sólo visualización)
├── pipeline.py          Procesamiento y cálculos (adaptado del notebook)
├── requirements.txt     Dependencias
├── .streamlit/config.toml   Colores y tema de la aplicación
└── data/FD_Energy_BASE_MVP.xlsx   Base de demostración
```

## Ejecutar en Mac

1. Abre **Terminal** y verifica que tienes Python 3.10 o superior:
   ```bash
   python3 --version
   ```
   Si no está instalado, descárgalo de https://www.python.org/downloads/macos/

2. Entra a la carpeta del proyecto (arrástrala a la Terminal después de escribir `cd `):
   ```bash
   cd ~/Downloads/fd_energy_mvp
   ```

3. Crea y activa un entorno virtual (sólo la primera vez se crea):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Instala las dependencias (sólo la primera vez):
   ```bash
   pip install -r requirements.txt
   ```

5. Ejecuta la aplicación:
   ```bash
   streamlit run app.py
   ```
   Se abrirá el navegador en http://localhost:8501. Para detenerla: `Control + C` en la Terminal.

La próxima vez sólo necesitas los pasos 2, `source .venv/bin/activate` y 5.

## Usar otro Excel

En la barra lateral elige **Cargar Excel**. El archivo debe tener las mismas hojas y columnas que la
base de demostración (empresa, planta, area, equipo, sistema_fv, medidor, tarifa_costo, regla_alerta,
lote_carga, lectura, alerta_generada). Las hojas `calc_` son opcionales y sólo se usan para validar.

## Modificar cálculos

Todos los cálculos están en `pipeline.py`. Los cambios respecto al notebook están marcados con
`[CAMBIO C1]` … `[CAMBIO C7]` y descritos al inicio del archivo. El parámetro `COBERTURA_MIN_REF`
(0.80) controla cuándo se muestra la variación de referencia por promedio diario.
