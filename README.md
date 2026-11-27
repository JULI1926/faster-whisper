# Faster Whisper SRT

Interfaz web local para transcribir audio y video con faster-whisper y generar subtítulos. Permite ajustar opciones de transcripción, guardar archivos de subtítulos y crear videos con los subtítulos integrados.

## Instalación

Sigue la [guía de instalación para Windows y Linux](INSTALACION.md). Allí encontrarás los requisitos, la configuración de FFmpeg y los pasos para preparar Python.

## Inicio rápido

Con el entorno virtual activado y las dependencias instaladas, ejecuta desde la carpeta del proyecto:

```bash
python3 tools/subtitle_ui.py
```

En Windows, usa `python tools/subtitle_ui.py`.

Abre [http://127.0.0.1:8765](http://127.0.0.1:8765) en tu navegador. La primera transcripción puede tardar mientras se descarga el modelo seleccionado.

## Gemini (opcional)

La función para generar descripciones y capítulos usa Gemini. Copia `.env.example` a `.env` y agrega tu clave `GEMINI_API_KEY`. La clave se mantiene local y `.env` está excluido de Git.

## Carpetas principales

- `tools/`: interfaz y servidor local.
- `faster_whisper/`: motor de transcripción.
- `tests/`: pruebas del proyecto.
- `fonts/`: tipografía usada al generar subtítulos.
- `exports/`, `ui_uploads/` y `subtitles/`: archivos generados localmente; no se suben al repositorio.

Este proyecto utiliza [faster-whisper](https://github.com/SYSTRAN/faster-whisper), basado en CTranslate2.
