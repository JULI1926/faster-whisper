# Instalación y uso (Windows y Linux)

Esta guía instala el proyecto y su interfaz web para crear subtítulos. La transcripción se ejecuta en el equipo; la primera vez que uses un modelo, faster-whisper lo descargará.

## Requisitos

- Python 3.10, 3.11 o 3.12 de 64 bits.
- Git.
- FFmpeg, necesario para incrustar los subtítulos en un video.
- Espacio en disco y conexión a Internet para instalar dependencias y descargar modelos.

La instalación básica usa CPU. Para usar una GPU NVIDIA se requieren además controladores y bibliotecas CUDA compatibles; consulta la sección [GPU del README](README.md#gpu).

## 1. Descargar el proyecto

Si todavía no tienes una copia, abre una terminal y ejecuta:

```bash
git clone https://github.com/JULI1926/faster-whisper.git
cd faster-whisper
```

Si ya tienes el proyecto, abre una terminal dentro de su carpeta y continúa con el paso 2.

## 2. Instalar FFmpeg

FFmpeg debe poder ejecutarse desde una terminal con el comando `ffmpeg`.

### Windows

Descarga FFmpeg desde [ffmpeg.org](https://ffmpeg.org/download.html), descomprime el paquete y agrega la carpeta `bin` de FFmpeg al `Path` de Windows. Cierra y vuelve a abrir PowerShell después del cambio.

### Ubuntu y Debian

```bash
sudo apt update
sudo apt install -y ffmpeg python3-venv python3-pip git
```

En otras distribuciones, instala FFmpeg y Python 3 con el gestor de paquetes de esa distribución.

Comprueba que está disponible:

```text
ffmpeg -version
```

## 3. Crear el entorno e instalar el proyecto

Ejecuta estos comandos desde la carpeta `faster-whisper`.

### Windows (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install whisper-ctranslate2
```

Si PowerShell impide activar el entorno, usa **Símbolo del sistema (cmd)**:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install whisper-ctranslate2
```

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install whisper-ctranslate2
```

La instalación de dependencias puede tardar y ocupar varios gigabytes, especialmente por PyTorch.

## 4. Iniciar la interfaz

Con el entorno virtual activado, desde la carpeta del proyecto ejecuta:

```bash
python tools/subtitle_ui.py
```

Abre [http://127.0.0.1:8765](http://127.0.0.1:8765) en el navegador. Para cerrar el servidor, vuelve a la terminal y presiona `Ctrl+C`.

En Linux también puedes usar `make subtitulos` si tienes `make` instalado y activaste el entorno virtual.

## 5. Uso y archivos generados

1. Carga el video o audio desde la interfaz.
2. Selecciona las opciones de transcripción y formato.
3. Espera a que termine y descarga los subtítulos o el video procesado.

Los videos exportados quedan en `exports/`; las cargas temporales, en `ui_uploads/`; y los subtítulos, en `subtitles/`. Son carpetas locales y sus archivos se excluyen de Git para evitar subir videos y resultados pesados.

La generación de descripción SEO y capítulos usa Gemini y es opcional. Para activarla, crea un archivo `.env` en la raíz del proyecto con:

```dotenv
GEMINI_API_KEY=tu_clave_de_gemini
# Opcional:
GEMINI_MODEL=gemini-flash-latest
```

No compartas ni publiques el archivo `.env` o tu clave.

## Solución de problemas

- **`ffmpeg no está instalado`**: instala FFmpeg, agrégalo al `Path` y vuelve a abrir la terminal.
- **No existe `whisper-ctranslate2`**: activa el entorno virtual e instala el paquete con `python -m pip install whisper-ctranslate2`.
- **No se reconoce `python` en Windows**: instala Python 3.11 y habilita la opción para agregar Python al `Path`, o usa `py -3.11`.
- **La primera transcripción tarda**: el modelo elegido puede tener que descargarse antes de iniciar.
- **La interfaz indica que el puerto está ocupado**: cierra la instancia anterior o inicia con otro puerto. En Linux/macOS: `SUBTITLE_UI_PORT=8766 python tools/subtitle_ui.py`; en PowerShell: `$env:SUBTITLE_UI_PORT='8766'; python tools/subtitle_ui.py`.
