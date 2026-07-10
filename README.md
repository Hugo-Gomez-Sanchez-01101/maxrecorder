# Grabador de Reuniones de Teams (Sistema + Micrófono) con Transcripción y Resumen IA

Herramienta de escritorio para Windows que:

1. Graba **simultáneamente** el audio de salida de Windows (voces de los demás en la
   reunión, vía WASAPI loopback) y tu **micrófono**, y los mezcla en un único WAV con
   **alineación temporal correcta**.
2. Transcribe el audio con **faster-whisper** (modelo local, gratis, sin subir el audio
   a ningún servidor).
3. Genera opcionalmente un **resumen con IA** (Claude / Anthropic) sobre la transcripción,
   con puntos clave, decisiones y tareas.
4. Puede quedarse en **segundo plano (bandeja del sistema)** y avisarte con un popup
   cuando detecta que ha empezado una reunión de Teams, para grabar con un clic.

## Qué se corrigió respecto a la versión anterior

El desincronizado de las dos pistas venía de dos causas, ya corregidas:

- **Offset de arranque entre hilos**: el hilo del audio del sistema y el del micrófono
  no arrancan exactamente en el mismo instante. Ahora se registra el timestamp real
  (reloj de alta precisión) en que cada uno empieza a capturar, y se rellena con
  silencio el inicio de la pista que arrancó más tarde para que ambas queden alineadas
  al mismo instante cero.
- **Drift de reloj entre dispositivos**: cada dispositivo de audio tiene su propio
  reloj interno, que nunca coincide exactamente con la tasa de muestreo "nominal"
  anunciada por el driver (típico en grabaciones largas de 30-60 min, donde el desfase
  puede acumular varios cientos de ms o más). Ahora se mide la duración real
  (reloj de pared) de cada pista y se resamplea con la tasa "efectiva" real en lugar
  de la nominal, corrigiendo el drift.
- Además hay un **ajuste fino opcional por correlación cruzada** (usa la fuga de audio
  de los altavoces que suele colarse tenuemente en el micrófono) para corregir
  desfases residuales de milisegundos. Si usas auriculares (sin fuga), simplemente no
  encuentra correlación fiable y no toca nada — no puede empeorar el resultado.
- Se ha añadido también manejo de errores: si el micrófono o el audio del sistema
  fallan al abrirse (causa típica de "solo se graba un lado"), ahora aparece un aviso
  claro en pantalla en vez de fallar en silencio.

## Requisitos

- **Windows 10/11** (usa WASAPI, no funciona en Linux/Mac).
- **Python 3.10 u 11** recomendado.

## Instalación

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

`pywin32` y `pystray`/`Pillow` son opcionales: sin ellos la app funciona igual para
grabar/transcribir/resumir manualmente, pero no podrás usar la detección automática
de reuniones ni minimizar a la bandeja del sistema.

## Uso

```powershell
python grabador.py
```

### Modo manual
1. Elige el micrófono (el audio del sistema se detecta solo).
2. **Iniciar grabación** → habla / reúnete → **Detener**.
3. Se guardan 3 WAV en la carpeta configurada: la mezcla, y las dos pistas por separado
   (`_sistema.wav`, `_mic.wav`) por si quieres revisarlas o procesarlas aparte.
4. **Transcribir última grabación** (elige tamaño de modelo Whisper).
5. (Opcional) Pega tu **API Key de Anthropic** y pulsa **Generar resumen**.

### Modo automático (detección de reuniones de Teams)
1. Marca **"Detectar reuniones automáticamente y avisar"**.
2. La app vigila en segundo plano si Teams está en una llamada, combinando: proceso de
   Teams en ejecución + una ventana visible cuyo título contenga alguna palabra clave
   (por defecto: `reunión, reunion, meeting, llamada, call`).
3. Pulsa **"Minimizar a bandeja"** (o cierra la ventana con la X y elige "Sí" para
   minimizar) para dejarla corriendo en segundo plano sin estorbar.
4. Cuando detecta una reunión, aparece un aviso en la esquina inferior derecha con
   botones **Grabar** / **Ignorar**. Al pulsar "Grabar" empieza a grabar automáticamente.
5. Icono en la bandeja del sistema con menú: Abrir / Iniciar grabación / Detener
   grabación / Salir.

⚠️ **Importante sobre la detección**: es una heurística basada en el título de ventana
de Teams, **no** usa la API oficial de Microsoft Graph (que requeriría registrar una
app en Azure AD y permisos delegados — mucho más complejo para un uso personal). Puede
que en tu versión de Teams el título de la ventana de la llamada no contenga ninguna de
las palabras por defecto. Para ajustarlo:

- Entra en una reunión de prueba y mira el título de la ventana (Alt+Tab, o el
  Administrador de tareas → pestaña "Detalles"/"Procesos" → columna de título si la
  añades) para ver qué texto usa tu versión de Teams.
- Edita el campo "Palabras clave del título de ventana" con esos términos, separados
  por comas.
- Usa el botón **"Probar aviso"** para comprobar que el popup se ve y funciona bien,
  sin depender de que haya una reunión real en curso.

### Arrancar automáticamente con Windows (opcional)

Si quieres que la app esté siempre en modo standby vigilando reuniones sin tener que
abrirla a mano:

1. Crea un acceso directo a `grabador.py` (o a un `.bat` que active el venv y lo
   ejecute) y colócalo en la carpeta de inicio de Windows: `Win+R` → escribe
   `shell:startup` → pega el acceso directo ahí.
2. Marca la casilla de detección automática y minimiza a bandeja antes de cerrar sesión;
   si prefieres que arranque ya minimizada, dímelo y te añado un flag `--minimized`.

## API Key de Anthropic

Consíguela en https://console.anthropic.com/settings/keys. Puedes pegarla en el campo
de la app o definirla como variable de entorno para no escribirla cada vez:

```powershell
setx ANTHROPIC_API_KEY "tu-api-key-aqui"
```

## Notas técnicas

- La mezcla se hace en mono para simplificar la sincronización; las pistas
  individuales (`_sistema.wav`, `_mic.wav`) se guardan ya alineadas también, por si
  prefieres procesarlas por separado o pasarlas a un editor de audio.
- Si no se detecta el dispositivo de loopback, activa "Mezcla estéreo" en
  Panel de control > Sonido > Grabación, o instala el driver virtual gratuito
  **VB-Cable** como alternativa.
- Los modelos de Whisper se descargan automáticamente la primera vez que se usan.
- Transcripciones muy largas se truncan a ~400.000 caracteres antes de enviarse a la
  API por límite de contexto.

## Estructura de archivos

```
grabador-audio-ia/
├── grabador.py        # aplicación principal (GUI)
├── requirements.txt
└── README.md
```