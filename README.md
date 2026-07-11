# Max Recorder

Grabadora de reuniones de Microsoft Teams para **Windows**, con transcripción local.

## Qué hace

1. **Graba** a la vez el audio del sistema (las voces de los demás, vía WASAPI
   loopback) y tu micrófono, y los guarda mezclados y como pistas separadas, ya
   sincronizadas.
2. **Transcribe** en local con faster-whisper (nada sale de tu equipo):
   - Modo "Tú / Ellos": etiqueta quién habla usando las dos pistas separadas.
   - Marcas de tiempo por frase y texto en streaming con barra de progreso.
   - Transcripción automática opcional al detener la grabación.
   - Botón "Archivo..." para transcribir cualquier audio suelto (wav, mp3, m4a...).
3. **Detecta reuniones** de Teams (siempre activa): cuando entras en una llamada,
   muestra un aviso con botones Grabar / Ignorar. Con el botón "Segundo plano" la
   app se esconde en la bandeja del sistema y sigue vigilando.
4. **Arranque con Windows** opcional (en segundo plano), configurable en Ajustes.

El resumen de las transcripciones no lo hace esta app: se genera aparte mediante
una tarea programada de Claude sobre los `.txt` guardados.

## Archivos generados

- Grabaciones (por defecto `Documents\MaxRecorder\Records`):
  `reunion_<fecha>_<hora>.wav` (mezcla), `_sistema.wav` (los demás) y `_mic.wav` (tú).
- Transcripciones (por defecto `Documents\MaxRecorder\Transcripts`):
  `reunion_AAAA-MM-DD.txt`, o `weekly_AAAA-MM-DD.txt` si la reunión de Teams se
  llama "[Weekly] Hacking Team". Si ya existe una ese día, se añade `_2`, `_3`...

Ambas carpetas se pueden cambiar: la de grabaciones en la ventana principal y la
de transcripciones en Ajustes. Los ajustes persisten en `config.json`.

## Requisitos

- Windows 10/11 (usa WASAPI; no funciona en Linux/Mac).
- Python 3.11 (recomendado; es con el que está probado).

### Requisitos mínimos de hardware

| Componente | Mínimo | Recomendado |
|---|---|---|
| CPU | 4 núcleos x64 (2015 o posterior) | 8 núcleos con AVX2 |
| RAM | 4 GB libres (modelos tiny/base/small) | 8 GB o más (medium/large-v3) |
| Disco | 2 GB libres (app + modelo small) | 10 GB o más (modelos grandes y grabaciones) |
| Audio | Salida y micrófono con WASAPI | — |

Orientación: una grabación genera aproximadamente 1 GB por hora (las tres pistas
WAV). Los modelos de Whisper se descargan la primera vez que se usan y ocupan
desde ~75 MB (tiny) hasta ~3 GB (large-v3); la transcripción se ejecuta en CPU,
y con el modelo small en un equipo de 4 núcleos tarda aproximadamente la mitad
de la duración del audio.

## Instalación

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

Lanza siempre con el Python del venv; lo más cómodo es el lanzador incluido:

```powershell
.\Grabador.bat
```

Evita `python grabador.py` a secas, que puede coger el Python global sin las
librerías instaladas.

### Grabar y transcribir

1. Elige el micrófono (el audio del sistema se detecta solo).
2. Iniciar → reúnete → Detener. El procesado se hace en segundo plano.
3. Con "Auto al detener" marcado, la transcripción arranca sola y el `.txt`
   se guarda automáticamente en la carpeta de transcripciones. "Guardar .txt"
   permite además exportarla a otra ruta.

### Detección de reuniones

Siempre activa desde que se abre la app. Cuando detecta que estás en una llamada
de Teams, aparece el aviso en la esquina inferior derecha. El icono de la bandeja
tiene menú: Abrir / Iniciar grabación / Detener grabación / Salir.

### Ajustes

El botón "Ajustes" (arriba a la derecha) abre la ventana de configuración:
carpeta de transcripciones, intervalo de sondeo, palabras clave de respaldo,
arranque automático con Windows y prueba del aviso.

## Estructura del proyecto

```
Recorder/
├── grabador.py               # punto de entrada
├── Grabador.bat              # lanzador (usa el Python del venv)
├── requirements.txt
├── README.md
├── config.json               # ajustes persistentes (se crea solo; ignorado por git)
├── tools/
│   └── diag_teams.py         # diagnóstico de la detección de Teams
└── maxrecorder/              # código de la aplicación
    ├── config.py             # constantes y persistencia de ajustes
    ├── audio.py              # captura, sincronización y mezcla
    ├── transcription.py      # motor faster-whisper
    ├── detection.py          # detección de reuniones de Teams
    ├── autostart.py          # arranque con Windows
    └── ui/
        ├── theme.py          # paleta y widgets del tema oscuro
        ├── app.py            # ventana principal
        ├── settings.py       # ventana de ajustes
        └── popup.py          # aviso de reunión detectada
```

## Notas

- Si no se detecta el dispositivo de loopback, activa "Mezcla estéreo" en
  Panel de control > Sonido > Grabación, o instala el driver virtual VB-Cable.
- Si hubiera un cierre inesperado, se registra la traza en `crash.log`.
- `tools/diag_teams.py` lista las ventanas de Teams y el uso del micrófono por
  app, útil para verificar la detección.
