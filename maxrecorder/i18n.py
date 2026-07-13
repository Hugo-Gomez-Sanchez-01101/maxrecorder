"""Tiny i18n layer: tr(text) returns the UI text in the active language.

English literals are the keys; only Spanish needs a translation table, so any
string missing from the table falls back to the English original. The active
language also drives the transcription language and the AI summary language.
"""

LANG_CODES = {"English": "en", "Spanish": "es"}
LANG_NAMES = {code: name for name, code in LANG_CODES.items()}
DEFAULT_LANGUAGE = "es"

_current = DEFAULT_LANGUAGE


def set_language(code):
    global _current
    _current = code if code in LANG_NAMES else DEFAULT_LANGUAGE


def current_language():
    return _current


def tr(text):
    if _current == "es":
        return _ES.get(text, text)
    return text


_ES = {
    # ---- Main window ----
    "Max Recorder — Teams meeting transcription": "Max Recorder — transcripción de reuniones de Teams",
    "▾ BACKGROUND": "▾ SEGUNDO PLANO",
    "⚙ SETTINGS": "⚙ AJUSTES",
    "Recording": "Grabación",
    "●  START": "●  GRABAR",
    "■  STOP": "■  PARAR",
    "Microphone:": "Micrófono:",
    "(system audio is captured automatically, via WASAPI loopback)":
        "(el audio del sistema se captura automáticamente, por loopback WASAPI)",
    "Transcription · faster-whisper (local)": "Transcripción · faster-whisper (local)",
    "Model:": "Modelo:",
    "Language:": "Idioma:",
    "You / Them": "Yo / Ellos",
    "Timestamps": "Marcas de tiempo",
    "Transcribe on stop": "Transcribir al parar",
    "▶  TRANSCRIBE LAST": "▶  TRANSCRIBIR ÚLTIMA",
    "FILE...": "ARCHIVO...",
    "LOAD .TXT": "CARGAR .TXT",
    "SAVE .TXT": "GUARDAR .TXT",
    "✦ AI SUMMARY": "✦ RESUMEN IA",

    # ---- Statuses ----
    "Ready": "Listo",
    "Recording system + microphone": "Grabando sistema + micrófono",
    "Processing and aligning tracks...": "Procesando y alineando pistas...",
    "Saved: {}": "Guardado: {}",
    "Watching Teams...": "Vigilando Teams...",
    "Detection off": "Detección desactivada",
    "Detection unavailable (missing dependencies)": "Detección no disponible (faltan dependencias)",
    "Autostart enabled": "Arranque automático activado",
    "Autostart disabled": "Arranque automático desactivado",
    "Transcribing...": "Transcribiendo...",
    "Error transcribing": "Error al transcribir",
    "Transcription complete": "Transcripción completada",
    "Transcript loaded": "Transcripción cargada",
    "Saving before quitting...": "Guardando antes de salir...",
    "Error processing the recording": "Error al procesar la grabación",
    "Summarizing...": "Resumiendo...",
    "Summary ready": "Resumen listo",
    "Summary failed": "El resumen falló",
    "Summarizing with AI...": "Resumiendo con IA...",
    "Publishing to Notion...": "Publicando en Notion...",
    "Summary ready · published to Notion": "Resumen listo · publicado en Notion",
    "Summary ready · Notion skipped (missing credentials)":
        "Resumen listo · Notion omitido (faltan credenciales)",
    "Summary ready · Notion failed: {}": "Resumen listo · Notion falló: {}",
    "Summary failed: {}": "El resumen falló: {}",
    "{} segments": "{} segmentos",
    "saved {}": "guardado {}",
    "Loaded {}": "Cargado {}",
    "Speaker tracks unavailable; transcribing the mix":
        "Pistas por hablante no disponibles; se transcribe la mezcla",
    "Speaker labels are only available for the last recording of this session":
        "Las etiquetas de hablante solo están disponibles para la última grabación de esta sesión",
    "Loading model '{}'...": "Cargando el modelo '{}'...",
    "Transcribing {}": "Transcribiendo {}",

    # ---- Dialogs ----
    "Notice": "Aviso",
    "Error": "Error",
    "Missing dependency": "Falta una dependencia",
    "There is no transcript to summarize.": "No hay transcripción que resumir.",
    "There is no transcript to save.": "No hay transcripción que guardar.",
    "Set your Mistral API key in Settings > AI summary first.":
        "Configura primero tu API key de Mistral en Ajustes > Resumen con IA.",
    "Record and stop a recording first.": "Graba y detén una grabación primero.",
    "A transcription is already in progress.": "Ya hay una transcripción en curso.",
    "Transcription": "Transcripción",
    "Transcription error": "Error de transcripción",
    "Could not list devices:\n{}": "No se pudieron listar los dispositivos:\n{}",
    "Error starting recording": "Error al iniciar la grabación",
    "Error during recording": "Error durante la grabación",
    "Error stopping the recording": "Error al detener la grabación",
    "Warnings during recording": "Avisos durante la grabación",
    "Load error": "Error al cargar",
    "Could not read the file:\n{}": "No se pudo leer el archivo:\n{}",
    "Text": "Texto",
    "All files": "Todos los archivos",
    "Audio": "Audio",
    "All": "Todos",
    "Minimize": "Minimizar",
    "Minimize to the system tray and keep watching for meetings?\n(No = close the application completely)":
        "¿Minimizar a la bandeja del sistema y seguir vigilando reuniones?\n(No = cerrar la aplicación por completo)",
    "Recording in progress": "Grabación en curso",
    "A recording is in progress.\n\n- Yes: stop and save it before quitting.\n- No: discard it and quit (it is lost).\n- Cancel: don't close.":
        "Hay una grabación en curso.\n\n- Sí: detenerla y guardarla antes de salir.\n- No: descartarla y salir (se pierde).\n- Cancelar: no cerrar.",
    "Close": "Cerrar",
    "The recording could not be saved.\nClose anyway?":
        "No se pudo guardar la grabación.\n¿Cerrar de todas formas?",
    "Not available": "No disponible",
    "Autostart is only available on Windows.":
        "El arranque automático solo está disponible en Windows.",
    "Could not change autostart:\n{}": "No se pudo cambiar el arranque automático:\n{}",
    "Install PyAudioWPatch (Windows only):\npip install PyAudioWPatch":
        "Instala PyAudioWPatch (solo Windows):\npip install PyAudioWPatch",
    "Install faster-whisper:\npip install faster-whisper":
        "Instala faster-whisper:\npip install faster-whisper",
    "Install: pip install pystray pillow": "Instala: pip install pystray pillow",

    # ---- Summary window ----
    "AI Summary — Max Recorder": "Resumen IA — Max Recorder",
    "AI SUMMARY": "RESUMEN IA",
    "COPY MARKDOWN": "COPIAR MARKDOWN",
    "CLOSE": "CERRAR",
    "Copied to clipboard": "Copiado al portapapeles",

    # ---- Tray ----
    "Open": "Abrir",
    "Start recording": "Iniciar grabación",
    "Stop recording": "Detener grabación",
    "Quit": "Salir",

    # ---- Meeting popup ----
    "TEAMS MEETING DETECTED": "REUNIÓN DE TEAMS DETECTADA",
    "Do you want to start recording now?": "¿Quieres empezar a grabar ahora?",
    "●  RECORD": "●  GRABAR",
    "DISMISS": "DESCARTAR",

    # ---- Settings window ----
    "Settings — Max Recorder": "Ajustes — Max Recorder",
    "SETTINGS": "AJUSTES",
    "Appearance": "Apariencia",
    "Theme:": "Tema:",
    "(TE FIELD: light Teenage Engineering inspired look)":
        "(TE FIELD: estética clara inspirada en Teenage Engineering)",
    "Folders": "Carpetas",
    "Recordings:": "Grabaciones:",
    "Transcripts:": "Transcripciones:",
    "CHOOSE...": "ELEGIR...",
    "AI summary": "Resumen con IA",
    "The AI SUMMARY button shows a Markdown summary you can copy. It only needs the Mistral key.":
        "El botón RESUMEN IA muestra un resumen en Markdown que puedes copiar. Solo necesita la key de Mistral.",
    "Mistral API key (NVIDIA):": "API key de Mistral (NVIDIA):",
    "Mistral API key": "API key de Mistral",
    "Also publish the summary to a Notion calendar (optional)":
        "Publicar además el resumen en un calendario de Notion (opcional)",
    "Notion API key:": "API key de Notion:",
    "Notion API key": "API key de Notion",
    "Notion calendar link or ID:": "Enlace o ID del calendario de Notion:",
    "Notion calendar": "Calendario de Notion",
    "TEST CONNECTIONS": "PROBAR CONEXIONES",
    "Testing...": "Probando...",
    "Notion disabled": "Notion desactivado",
    "Missing dependency for this feature: pip install requests":
        "Falta una dependencia para esta función: pip install requests",
    "Background · Meeting detection": "Segundo plano · Detección de reuniones",
    "Automatically detect meetings and notify (always on)":
        "Detectar reuniones automáticamente y avisar (siempre activo)",
    "Poll (s):": "Sondeo (s):",
    "Keywords (title fallback):": "Palabras clave (títulos alternativos):",
    "Start automatically at Windows login (in the background)":
        "Arrancar automáticamente al iniciar Windows (en segundo plano)",
    "(not available on this platform)": "(no disponible en esta plataforma)",
    "TEST NOTIFICATION": "PROBAR NOTIFICACIÓN",
    "Missing dependencies for detection: pip install psutil pywin32":
        "Faltan dependencias para la detección: pip install psutil pywin32",
    "For background mode install: pip install pystray pillow":
        "Para el segundo plano instala: pip install pystray pillow",
    "SAVE AND CLOSE": "GUARDAR Y CERRAR",

    # ---- Settings help texts ----
    "How to get your Notion API key:\n\n"
    "1. Go to https://app.notion.com/developers/connections\n"
    "2. Create a new connection (integration): this gives you an access "
    "token — that is the API key.\n"
    "3. Give the connection access to the workspace that contains your "
    "calendar database.\n"
    "4. In Notion, open the page that contains the calendar, click the ... "
    "menu (top right) > Connections, and add your integration so it can "
    "access that page.":
        "Cómo obtener tu API key de Notion:\n\n"
        "1. Ve a https://app.notion.com/developers/connections\n"
        "2. Crea una nueva conexión (integración): te dará un token de "
        "acceso — esa es la API key.\n"
        "3. Da a la conexión acceso al espacio de trabajo que contiene tu "
        "calendario.\n"
        "4. En Notion, abre la página que contiene el calendario, pulsa el "
        "menú ... (arriba a la derecha) > Conexiones, y añade tu integración "
        "para que pueda acceder a esa página.",
    "How to get your Mistral API key (free, via NVIDIA):\n\n"
    "1. Go to https://build.nvidia.com/mistralai/mistral-medium-3.5-128b\n"
    "2. Sign in (create a free account if needed).\n"
    "3. Generate an API key and copy it here.":
        "Cómo obtener tu API key de Mistral (gratis, vía NVIDIA):\n\n"
        "1. Ve a https://build.nvidia.com/mistralai/mistral-medium-3.5-128b\n"
        "2. Inicia sesión (crea una cuenta gratuita si hace falta).\n"
        "3. Genera una API key y cópiala aquí.",
    "How to get your Notion calendar link:\n\n"
    "1. In Notion, next to the calendar database name, click the ... menu.\n"
    "2. Choose 'Copy link to view'.\n"
    "3. Paste the full link here (e.g. https://www.notion.so/3726c2...?v=...).\n\n"
    "The app extracts the database ID from the link automatically when you "
    "save. You can also paste the 32-character ID directly.":
        "Cómo obtener el enlace de tu calendario de Notion:\n\n"
        "1. En Notion, junto al nombre de la base de datos del calendario, "
        "pulsa el menú ...\n"
        "2. Elige 'Copiar enlace a la vista'.\n"
        "3. Pega aquí el enlace completo (p. ej. https://www.notion.so/3726c2...?v=...).\n\n"
        "La aplicación extrae el ID de la base de datos automáticamente al "
        "guardar. También puedes pegar directamente el ID de 32 caracteres.",
}
