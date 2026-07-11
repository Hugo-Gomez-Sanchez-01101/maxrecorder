# Max Recorder — Grabador de reuniones de Teams (Sistema + Micrófono) con Transcripción

Herramienta de escritorio para **Windows** que:

1. Graba **simultáneamente** el audio de salida de Windows (las voces de los demás en la
   reunión de Teams, vía **WASAPI loopback**) y tu **micrófono**, y los mezcla en un único
   WAV con **alineación temporal correcta**. También guarda cada pista por separado.
2. Transcribe el audio con **faster-whisper** (modelo local, gratis, sin subir nada a
   ningún servidor):
   - **Modo "Tú / Ellos"**: transcribe las pistas `_mic` y `_sistema` por separado y
     entrelaza los segmentos por tiempo, etiquetando quién habla (sin necesidad de
     pyannote ni token de HuggingFace).
   - **Marcas de tiempo** por segmento y **streaming**: el texto va apareciendo según
     se transcribe, con barra de progreso.
   - **Filtro VAD**: salta los silencios (más rápido y evita alucinaciones del modelo
     en tramos sin voz).
   - **Caché del modelo**: se carga una sola vez y se reutiliza en transcripciones
     siguientes.
   - **Autoguardado**: el `.txt` se guarda solo en la **carpeta de transcripciones**
     (configurable en Ajustes) como `reunion_AAAA-MM-DD.txt` — o `weekly_AAAA-MM-DD.txt`
     si la reunión de Teams se llama "[Weekly] Hacking Team"; si ya existe uno ese día
     se añade `_2`, `_3`... Opcionalmente se **transcribe automáticamente al detener**
     la grabación (casilla "Auto al detener").
   - Botón **"Archivo..."** para transcribir cualquier audio suelto (wav/mp3/m4a/...).
3. La **detección de reuniones está siempre activa**: cuando detecta que estás en una
   reunión de Teams te avisa con un popup para grabar con un clic. Con el botón
   **"Segundo plano"** (arriba a la derecha) la app se esconde en la bandeja del sistema
   y sigue vigilando.
4. Puede **arrancar automáticamente al iniciar sesión** (en segundo plano), para no
   depender de abrirla a mano.
5. Ventana de **Ajustes** (botón "⚙ Ajustes"): carpeta de transcripciones, palabras
   clave de respaldo, intervalo de sondeo, arranque con Windows y probar el aviso. Los
   ajustes persisten entre sesiones en `config.json` (junto al script, ignorado por git).

Por defecto, las grabaciones se guardan en `%USER%\Documents\MaxRecorder\Records` y las
transcripciones en `%USER%\Documents\MaxRecorder\Transcripts`; ambas carpetas se pueden
cambiar (la de grabaciones en la ventana principal, la de transcripciones en Ajustes).

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
4. Si la casilla **"Auto al detener"** está marcada, la transcripción arranca sola al
   guardar; si no, pulsa **Transcribir última** (elige el tamaño del modelo Whisper).
   Con **"Tú / Ellos"** marcado se transcriben las dos pistas por separado y el texto
   sale etiquetado por hablante y con marcas de tiempo.
5. El `.txt` se guarda automáticamente en la carpeta de transcripciones con el nombre
   `reunion_AAAA-MM-DD.txt` (o `weekly_AAAA-MM-DD.txt`, ver arriba); **Guardar .txt**
   permite además exportarlo donde quieras (tu tarea de Claude se encarga del resumen).

### Detección de reuniones (siempre activa)
1. La app vigila desde que se abre si Teams está usando el micrófono (en llamada);
   no hay que activar nada.
2. Cuando lo detecta, aparece un aviso en la esquina inferior derecha con botones
   **Grabar** / **Ignorar**.
3. El botón **"Segundo plano"** (arriba a la derecha) esconde la ventana en la bandeja
   del sistema; el icono tiene menú: Abrir / Iniciar grabación / Detener grabación /
   Salir.
4. Las palabras clave de respaldo y el intervalo de sondeo se cambian en **Ajustes**.

### Arrancar automáticamente al iniciar sesión
Marca la casilla **"Arrancar automáticamente al iniciar sesión de Windows"** en
**Ajustes**. Escribe una entrada en `HKCU\...\Run` (por usuario, **sin admin**) que lanza
la app con `--tray` (oculta en la bandeja y con la detección ya activada). Para
desactivarlo, desmarca la casilla.

> Marca la casilla **ejecutando la app desde `Grabador.bat`**, para que la entrada del
> registro apunte al `pythonw.exe` del venv (con todas las dependencias) y no al Python
> global.

## Separación de hablantes ("Tú / Ellos")

Ya integrada, sin dependencias extra: como la app graba tu micrófono y el audio del
sistema en **pistas separadas y alineadas**, basta transcribir cada pista por su lado y
entrelazar los segmentos por su marca de tiempo. Tu voz sale como **"Tú"** y el resto de
participantes como **"Ellos"**. Es la casilla "Tú / Ellos" de la sección de transcripción
(activada por defecto; solo aplica a grabaciones hechas con la app, que tienen las dos
pistas).

Para distinguir entre varios participantes remotos ("Hablante 1/2/3") haría falta
diarización real de `_sistema.wav` con **pyannote.audio** (torch + token de HuggingFace);
queda como posible mejora futura.

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
├── config.json       # ajustes persistentes (se crea solo; ignorado por git)
├── requirements.txt
└── README.md
```
