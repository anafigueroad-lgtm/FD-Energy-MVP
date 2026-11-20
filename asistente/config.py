"""
Configuración del asistente de FD Energy.

Los datos de contacto se pueden sobrescribir sin tocar el código desde los «secrets» de Streamlit
(.streamlit/secrets.toml en local o el panel Settings → Secrets en Streamlit Community Cloud):

    [asistente]
    correo = "energy@fdmexico.com"
    whatsapp = "52XXXXXXXXXX"     # formato internacional, sólo dígitos (sin +, espacios ni guiones)

Si un canal queda vacío, su botón no se muestra.
"""

CONTACTO = {
    "correo": "energy@fdmexico.com",
    "whatsapp": "",          # pendiente: sin número configurado no se muestra el botón de WhatsApp
    "asunto_correo": "Consulta desde el dashboard FD Energy",
}

# Umbrales de coincidencia del motor de preguntas frecuentes (0 a 1).
UMBRAL_RESPUESTA = 0.55      # a partir de aquí responde directamente
UMBRAL_SUGERENCIA = 0.33     # entre ambos umbrales pregunta «¿Te refieres a…?»
MAX_SUGERENCIAS = 3

# Ventana flotante
VENTANA_ANCHO_PX = 380        # ancho de la ventana (se limita al 92 % de la pantalla en móviles)
VENTANA_ALTO_MENSAJES_PX = 260  # alto del área de mensajes (con scroll interno)
BOTON_MARGEN_PX = 24          # distancia del botón a la esquina inferior derecha
RESPUESTA_LARGA_CARACTERES = 650  # respuestas más largas se muestran con «Ver respuesta completa»
