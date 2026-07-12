# Max Recorder

Microsoft Teams meeting recorder for **Windows**, with local transcription.

## What it does

1. **Records** the system audio (the other people's voices, via WASAPI loopback)
   and your microphone at the same time, and saves a single file to disk with the
   mix already synchronized.
2. **Transcribes** locally with faster-whisper (nothing leaves your machine):
   - "You / Them" mode: labels who is speaking by separating the two tracks (kept
     in a temporary folder only while they are needed).
   - Timestamps per line and streaming text with a progress bar.
   - Optional automatic transcription when the recording stops.
   - "File..." button to transcribe any standalone audio file (wav, mp3, m4a...).
3. **Detects meetings** in Teams (always on): when you join a call, it shows a
   notification with Record / Dismiss buttons. With the "Background" button the
   app hides in the system tray and keeps watching.
4. **Start with Windows** optionally (in the background), configurable in Settings.

Summarizing the transcripts is not done by this app: it is generated separately by
a scheduled Claude task over the saved `.txt` files.

## Generated files

- Recordings (by default `Documents\MaxRecorder\Records`):
  `meeting_<date>_<time>.wav` with the final mix.
- Transcripts (by default `Documents\MaxRecorder\Transcripts`):
  `meeting_YYYY-MM-DD.txt`, or `weekly_YYYY-MM-DD.txt` if the Teams meeting is
  named "[Weekly] Hacking Team". If one already exists that day, `_2`, `_3`... is
  appended.

Both folders are configured in the Settings window. Settings persist in
`config.json`.

## Requirements

- Windows 10/11 (uses WASAPI; does not work on Linux/Mac).
- Python 3.11 (recommended; it is what it was tested with).

### Minimum hardware requirements

| Component | Minimum | Recommended |
|---|---|---|
| CPU | 4-core x64 (2015 or newer) | 8 cores with AVX2 |
| RAM | 4 GB free (tiny/base/small models) | 8 GB or more (medium/large-v3) |
| Disk | 2 GB free (app + small model) | 10 GB or more (large models and recordings) |
| Audio | Output and microphone with WASAPI | — |

Guidance: the mix WAV takes about 300 MB per hour (plus some temporary space
while transcribing with "You / Them" mode). The Whisper models are downloaded the
first time they are used and take from ~75 MB (tiny) to ~3 GB (large-v3);
transcription runs on the CPU, and with the small model on a 4-core machine it
takes roughly half the audio duration.

## Installation

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

Always launch with the venv's Python; the easiest way is the included launcher:

```powershell
.\Grabador.bat
```

Avoid a plain `python grabador.py`, which may pick up the global Python without
the installed libraries.

### Record and transcribe

1. Choose the microphone (system audio is detected automatically).
2. Start → have your meeting → Stop. Processing happens in the background.
3. With "Transcribe on stop" checked, transcription starts by itself and the
   `.txt` is saved automatically in the transcripts folder. "Save .txt" also lets
   you export it to another path.

### Meeting detection

Always on from the moment the app opens. When it detects that you are in a Teams
call, the notification appears in the bottom-right corner. The tray icon has a
menu: Open / Start recording / Stop recording / Quit.

### Settings

The "Settings" button (top right) opens the configuration window: recordings and
transcripts folders, poll interval, fallback keywords, automatic startup with
Windows, and testing the notification.

## Project structure

```
MaxRecorder/
├── grabador.py               # entry point
├── Grabador.bat              # launcher (uses the venv's Python)
├── requirements.txt
├── README.md
├── config.json               # persistent settings (created automatically; git-ignored)
├── tools/
│   └── diag_teams.py         # Teams detection diagnostics
└── maxrecorder/              # application code
    ├── config.py             # constants and settings persistence
    ├── audio.py              # capture, synchronization and mixing
    ├── transcription.py      # faster-whisper engine
    ├── detection.py          # Teams meeting detection
    ├── autostart.py          # start with Windows
    └── ui/
        ├── theme.py          # dark-theme palette and widgets
        ├── app.py            # main window
        ├── settings.py       # settings window
        └── popup.py          # detected-meeting notification
```

## Notes

- If the loopback device is not detected, enable "Stereo Mix" in
  Control Panel > Sound > Recording, or install the VB-Cable virtual driver.
- If there is an unexpected shutdown, the traceback is logged in `crash.log`.
- `tools/diag_teams.py` lists the Teams windows and the per-app microphone usage,
  useful to verify detection.
