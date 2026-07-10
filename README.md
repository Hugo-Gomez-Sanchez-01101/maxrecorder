# Max Recorder — Grabador de reuniones de Teams (Sistema + Micrófono) con Transcripción

Herramienta de escritorio para **Windows** que:

1. Graba **simultáneamente** el audio de salida de Windows (las voces de los demás en la
   reunión de Teams, vía **WASAPI loopback**) y tu **micrófono**, y los mezcla en un único
   WAV con **alineación temporal correcta**. También guarda cada pista por separado.
2. Transcribe el audio con **faster-whisper** (modelo local, gratis, sin subir nada a
   ningún servidor).
3. Puede quedarse en **segundo plano (bandeja del sistema)** y avisarte con un popup
   cuando detecta que estás en una reunión de Teams, para grabar con un clic.
4. Puede **arrancar automáticamente al iniciar sesión** (minimizada a la bandeja), para
   no depender de abrirla a mano.

> El **resumen** de las transcripciones no lo hace esta app: se genera aparte mediante una
> tarea programada de Claude sobre los `.txt` guardados.

## Detección de reuniones (por uso del micrófono)

La app considera que estás **en una reunión/llamada** cuando **Teams está usando el
micrófono** en ese momento. Windows registra ese estado por app en
`CapabilityAccessManager\ConsentStore\microphone` (`LastUsedTimeStop == 0` ⇒ en uso ahora).
Teams toma el micro al entrar en la llamada y lo suelta al salir, así que es una señal
**fiable** y **no depende del título de la ventana** (que solo lleva el nombre de la
reunión). Como respaldo, si no hubiera acceso al registro, cae al método antiguo por
palabra clave en el título de la ventana de Teams.

## Sincronización de las dos pistas

- **Offset de arranque entre hilos**: se registra el timestamp real (reloj de alta
  precisión) en que cada pista empieza a capturar y se rellena con silencio el inicio de
  la que arrancó más tarde, para alinearlas al mismo instante cero.
- **Resample por tasa nominal**: se resamplea a 44,1 kHz usando la tasa nominal del
  dispositivo. (Nota: WASAPI loopback **no entrega muestras durante los silencios**, por
  lo que "muestras/duración real" daría una tasa efectiva falseada y el audio del sistema
  saldría ralentizado; por eso se usa la nominal.)
- **Ajuste fino opcional por correlación cruzada**: aprovecha la fuga tenue del altavoz
  que suele colarse en el micrófono para corregir desfases residuales de milisegundos. Si
  usas auriculares (sin fuga), no encuentra correlación fiable y no toca nada.
- **Manejo de errores**: si el micrófono o el audio del sistema fallan al abrirse, aparece
  un aviso claro en pantalla en vez de fallar en silencio.

## Requisitos

- **Windows 10/11** (usa WASAPI; no funciona en Linux/Mac).
- **Python 3.11** (recomendado; es con el que está probado).

## Instalación

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

Lanza **siempre** con el Python del venv. Lo más cómodo es el lanzador incluido:

```powershell
.\Grabador.bat
```

`Grabador.bat` usa `venv\Scripts\pythonw.exe` (sin ventana de consola) y garantiza que se
cargan todas las dependencias. Evita `python grabador.py` a secas, que puede coger el
Python global sin las librerías instaladas.

### Modo manual
1. Elige el micrófono (el audio del sistema se detecta solo vía loopback).
2. **Iniciar grabación** → reúnete → **Detener**. El procesado y guardado se hacen en
   segundo plano (no congela la ventana).
3. Se guardan 3 WAV en la carpeta configurada: la **mezcla** y las dos pistas por separado
   (`_sistema.wav` = los demás, `_mic.wav` = tú), ya alineadas.
4. **Transcribir última grabación** (elige el tamaño del modelo Whisper).
5. **Guardar .txt** de la transcripción (tu tarea de Claude se encarga del resumen).

### Modo automático (detección de reuniones)
1. Marca **"Detectar reuniones automáticamente y avisar"**.
2. La app vigila en segundo plano si Teams está usando el micrófono (en llamada).
3. Cuando lo detecta, aparece un aviso en la esquina inferior derecha con botones
   **Grabar** / **Ignorar**.
4. Icono en la bandeja con menú: Abrir / Iniciar grabación / Detener grabación / Salir.
   Usa **"Minimizar a bandeja"** para dejarla corriendo sin estorbar.

### Arrancar automáticamente al iniciar sesión
Marca la casilla **"Arrancar automáticamente al iniciar sesión (minimizado a la
bandeja)"**. Escribe una entrada en `HKCU\...\Run` (por usuario, **sin admin**) que lanza
la app con `--tray` (oculta en la bandeja y con la detección ya activada). Para
desactivarlo, desmarca la casilla.

> Marca la casilla **ejecutando la app desde `Grabador.bat`**, para que la entrada del
> registro apunte al `pythonw.exe` del venv (con todas las dependencias) y no al Python
> global.

## Diarización de hablantes (en preparación)

Objetivo: transcripción con etiquetas de hablante ("Tú" / "Hablante 1/2/3"), estilo Notion,
diarizando `_sistema.wav` (los demás) con **pyannote.audio** y etiquetando `_mic.wav` como
"Tú". Requiere `torch` + `pyannote.audio` (ya instalables desde `requirements.txt`) y un
**token gratuito de HuggingFace** con los términos aceptados de los modelos
`pyannote/segmentation-3.0`, `pyannote/speaker-diarization-3.1` y
`pyannote/speaker-diarization-community-1`. El token se lee de la variable de entorno
`HF_TOKEN`. La integración del botón en la app está pendiente.

## Notas técnicas

- La mezcla se hace en mono para simplificar la sincronización; las pistas individuales se
  guardan ya alineadas por si prefieres procesarlas por separado.
- Si no se detecta el dispositivo de loopback, activa "Mezcla estéreo" en
  Panel de control > Sonido > Grabación, o instala el driver virtual gratuito **VB-Cable**.
- Los modelos de Whisper se descargan automáticamente la primera vez que se usan.
- Si hubiera un cierre inesperado, se registra la traza en `crash.log` (faulthandler).
- `diag_teams.py` es una utilidad de diagnóstico: lista las ventanas de Teams y el uso del
  micrófono por app (útil para verificar la detección).

## Estructura de archivos

```
Recorder/
├── grabador.py       # aplicación principal (GUI)
├── Grabador.bat      # lanzador (usa el Python del venv)
├── diag_teams.py     # utilidad de diagnóstico de detección
├── requirements.txt
└── README.md
```
