#!/usr/bin/env python3
import errno
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning, module="cgi")

import cgi
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import json
import urllib.request
from dotenv import load_dotenv
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# Cargar variables de entorno
load_dotenv()

def get_gemini_url():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("La variable de entorno GEMINI_API_KEY no está configurada.")
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = ROOT / "ui_uploads"
OUTPUT_DIR = ROOT / "subtitles"
FONTS_DIR = ROOT / "fonts"
EXPORT_DIR = ROOT / "exports"
VENV_BIN = ROOT / ".venv-linux" / "bin"
WHISPER_BIN = VENV_BIN / "whisper-ctranslate2"
FFMPEG_BIN = shutil.which("ffmpeg")

JOBS = {}
JOBS_LOCK = threading.Lock()


def generate_youtube_seo_with_gemini(srt_content):
    prompt = f"""
    Analiza la siguiente transcripción de un video (en formato SRT).
    1. Escribe una descripción de YouTube extensa (3 párrafos) optimizada para SEO.
    2. Extrae los capítulos importantes y asígnales los tiempos de inicio en formato [MM:SS].
    3. Formato de salida:
    ---
    [Descripción SEO]
    ---
    *Capítulos:*
    [MM:SS] - Título del capítulo
    ---

    Transcripción:
    {srt_content[:15000]}
    """
    
    data = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    req = urllib.request.Request(
        get_gemini_url(), 
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode("utf-8"))
        return result["candidates"][0]["content"]["parts"][0]["text"]

def safe_filename(name):
    name = Path(name or "video").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "video"


def bool_arg(value):
    return "True" if value in ("true", "on", "1", "yes", True) else "False"


def int_option(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def space_words(line, spaces):
    separator = " " * spaces
    return re.sub(r"(?<=\S) +(?=\S)", separator, line)


def apply_word_spacing(path, spaces):
    if spaces <= 1 or path.suffix.lower() not in (".srt", ".vtt", ".txt"):
        return

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    processed = []
    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        lower_content = content.lower()
        is_metadata = (
            not content.strip()
            or content.strip().isdigit()
            or "-->" in content
            or lower_content.startswith("webvtt")
            or lower_content.startswith("note")
        )
        processed.append((content if is_metadata else space_words(content, spaces)) + ending)

    path.write_text("".join(processed), encoding="utf-8")


def parse_timestamp(value):
    match = re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})$", value.strip())
    if not match:
        raise ValueError(f"Invalid subtitle timestamp: {value}")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) + milliseconds / 1000


def format_timestamp(seconds, separator):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02}{separator}{milliseconds:03}"


def chunk_words(text, max_words):
    words = text.split()
    return [" ".join(words[index : index + max_words]) for index in range(0, len(words), max_words)]


def split_timed_text(start, end, text, max_words):
    chunks = chunk_words(text, max_words)
    if len(chunks) <= 1:
        return [(start, end, text)]

    counts = [len(chunk.split()) for chunk in chunks]
    total = sum(counts)
    duration = max(0, end - start)
    cues = []
    cursor = start
    elapsed_words = 0
    for index, (chunk, count) in enumerate(zip(chunks, counts)):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            elapsed_words += count
            chunk_end = start + duration * elapsed_words / total
        cues.append((cursor, chunk_end, chunk))
        cursor = chunk_end
    return cues


def split_srt_blocks(content, max_words):
    blocks = re.split(r"\n\s*\n", content.strip())
    output = []
    cue_number = 1
    timing_pattern = re.compile(r"(.+?)\s+-->\s+(.+)")

    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue

        timing_index = 1 if lines[0].strip().isdigit() and len(lines) > 2 else 0
        timing_match = timing_pattern.match(lines[timing_index])
        if not timing_match:
            output.append(block)
            continue

        start_raw, end_raw = timing_match.groups()
        settings = ""
        if " " in end_raw.strip():
            end_raw, settings = end_raw.strip().split(" ", 1)
            settings = " " + settings

        text = " ".join(line.strip() for line in lines[timing_index + 1 :])
        separator = "," if "," in start_raw else "."
        cues = split_timed_text(
            parse_timestamp(start_raw),
            parse_timestamp(end_raw),
            text,
            max_words,
        )

        for cue_start, cue_end, cue_text in cues:
            output.append(
                "\n".join(
                    [
                        str(cue_number),
                        (
                            f"{format_timestamp(cue_start, separator)} --> "
                            f"{format_timestamp(cue_end, separator)}{settings}"
                        ),
                        cue_text,
                    ]
                )
            )
            cue_number += 1

    return "\n\n".join(output) + "\n"


def split_vtt_blocks(content, max_words):
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header = []
    while lines and not "-->" in lines[0]:
        header.append(lines.pop(0))
        if lines and not lines[0].strip():
            header.append(lines.pop(0))
            break

    body = "\n".join(lines).strip()
    blocks = re.split(r"\n\s*\n", body) if body else []
    output = header[:]
    timing_pattern = re.compile(r"(.+?)\s+-->\s+(.+)")

    for block in blocks:
        block_lines = [line for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(block_lines) if "-->" in line), None)
        if timing_index is None:
            output.extend([block, ""])
            continue

        timing_match = timing_pattern.match(block_lines[timing_index])
        if not timing_match:
            output.extend([block, ""])
            continue

        start_raw, end_raw = timing_match.groups()
        settings = ""
        if " " in end_raw.strip():
            end_raw, settings = end_raw.strip().split(" ", 1)
            settings = " " + settings

        text = " ".join(line.strip() for line in block_lines[timing_index + 1 :])
        cues = split_timed_text(
            parse_timestamp(start_raw),
            parse_timestamp(end_raw),
            text,
            max_words,
        )

        for cue_start, cue_end, cue_text in cues:
            output.extend(
                [
                    (
                        f"{format_timestamp(cue_start, '.')} --> "
                        f"{format_timestamp(cue_end, '.')}{settings}"
                    ),
                    cue_text,
                    "",
                ]
            )

    return "\n".join(output).rstrip() + "\n"


def enforce_max_words_per_subtitle(path, max_words):
    if max_words < 1 or path.suffix.lower() not in (".srt", ".vtt"):
        return

    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".srt":
        processed = split_srt_blocks(content, max_words)
    else:
        processed = split_vtt_blocks(content, max_words)
    path.write_text(processed, encoding="utf-8")


def subtitle_word_stats(path):
    if path.suffix.lower() not in (".srt", ".vtt"):
        return None

    content = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip())
    counts = []
    for block in blocks:
        text_lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.isdigit()
                or "-->" in stripped
                or stripped.lower() == "webvtt"
                or stripped.lower().startswith("note")
            ):
                continue
            text_lines.append(stripped)
        if text_lines:
            counts.append(len(" ".join(text_lines).split()))

    if not counts:
        return {"blocks": 0, "max_words": 0, "over_limit": 0}

    return {
        "blocks": len(counts),
        "max_words": max(counts),
    }


def cuda_library_path():
    paths = [
        ROOT / ".venv-linux/lib/python3.12/site-packages/nvidia/cublas/lib",
        ROOT / ".venv-linux/lib/python3.12/site-packages/nvidia/cudnn/lib",
        ROOT / ".venv-linux/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib",
    ]
    return ":".join(str(path) for path in paths if path.exists())


def make_command(video_path, options):
    mode = options.get("split_mode", "words")
    word_timestamps = options.get("word_timestamps", "true")
    if mode in ("words", "width"):
        word_timestamps = "true"

    command = [
        str(WHISPER_BIN),
        str(video_path),
        "--model",
        options.get("model", "large-v3"),
        "--device",
        options.get("device", "cuda"),
        "--compute_type",
        options.get("compute_type", "float16"),
        "--task",
        options.get("task", "transcribe"),
        "--language",
        options.get("language", "es"),
        "--output_format",
        options.get("output_format", "srt"),
        "--output_dir",
        str(OUTPUT_DIR),
        "--word_timestamps",
        bool_arg(word_timestamps),
        "--beam_size",
        options.get("beam_size", "5"),
    ]

    if mode == "words":
        command += ["--max_words_per_line", options.get("max_words_per_line", "3")]
    elif mode == "width":
        command += [
            "--max_line_width",
            options.get("max_line_width", "42"),
            "--max_line_count",
            options.get("max_line_count", "2"),
        ]

    if options.get("vad_filter") in ("true", "on", "1", "yes"):
        command += ["--vad_filter", "True"]

    batch_size = options.get("batch_size", "").strip()
    if batch_size:
        command += ["--batch_size", batch_size]

    initial_prompt = options.get("initial_prompt", "").strip()
    if initial_prompt:
        command += ["--initial_prompt", initial_prompt]

    return command


ASS_FONT_MAP = {
    "sans": "DejaVu Sans",
    "serif": "DejaVu Serif",
    "rounded": "DejaVu Sans",
    "condensed": "DejaVu Sans",
    "mono": "DejaVu Sans Mono",
    "payday": "Payday",
}

POSITION_ALIGNMENT = {
    "top": "8",
    "middle": "5",
    "bottom": "2",
}


def caption_layout(style):
    """Return (alignment, margin_l, margin_r, margin_v) for a caption style.

    Preset positions map to ASS alignment numbers. A "custom" position maps to
    Alignment=7 (top-left) with MarginL/MarginV derived from percentage
    coordinates over the video frame (PlayRes 384x288 used by the subtitles
    filter), so the preview drag position matches the exported render.
    """
    position = style.get("position", "bottom")
    if position == "custom":
        try:
            x = float(style.get("pos_x", 50))
            y = float(style.get("pos_y", 92))
        except (TypeError, ValueError):
            x, y = 50.0, 92.0
        x = max(0.0, min(100.0, x))
        y = max(0.0, min(100.0, y))
        return "7", round(x / 100 * 384), 0, round(y / 100 * 288)
    return POSITION_ALIGNMENT.get(position, "2"), 10, 10, 24


def ass_color(hex_color):
    value = (hex_color or "#ffffff").lstrip("#")
    if len(value) != 6 or not all(char in "0123456789abcdefABCDEF" for char in value):
        value = "ffffff"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}".upper()


def ass_color_alpha(hex_color, alpha):
    value = (hex_color or "#ffffff").lstrip("#")
    if len(value) != 6 or not all(char in "0123456789abcdefABCDEF" for char in value):
        value = "ffffff"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def ass_timestamp(seconds):
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, remainder = divmod(remainder, 1000)
    centiseconds = remainder // 10
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def ass_escape_text(text):
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def parse_srt_segments(content):
    segments = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        index = 1 if lines[0].strip().isdigit() else 0
        match = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})",
            lines[index],
        )
        if not match:
            continue
        start = parse_timestamp(match.group(1))
        end = parse_timestamp(match.group(2))
        text = "\n".join(lines[index + 1 :])
        segments.append((start, end, text))
    return segments


def karaoke_timed_text(text, start, end):
    tokens = [token for token in re.split(r"(\s+)", text) if token != ""]
    words = [token for token in tokens if not re.fullmatch(r"\s+", token)]
    total = sum(len(word) for word in words) or 1
    duration = max(0.001, end - start)
    parts = []
    for token in tokens:
        if re.fullmatch(r"\s+", token):
            parts.append(token.replace("\n", "\\N"))
        else:
            centiseconds = max(0, int(round(len(token) / total * duration * 100)))
            parts.append("{\\k%d}%s" % (centiseconds, ass_escape_text(token)))
    return "".join(parts)


def build_karaoke_ass(srt_content, style):
    font_key = style.get("font", "sans")
    font_name = ASS_FONT_MAP.get(font_key, "DejaVu Sans")

    try:
        ratio = max(0.7, min(2.0, float(style.get("font_size", 1) or 1)))
    except (TypeError, ValueError):
        ratio = 1.0
    size = max(6, min(40, round(11 * ratio)))

    color = style.get("color", "#ffffff")
    primary = ass_color(color)
    secondary = ass_color_alpha(color, 0x61)
    alignment, margin_l, margin_r, margin_v = caption_layout(style)

    try:
        stroke_px = max(0, min(40, int(float(style.get("outline", 0) or 0))))
    except (TypeError, ValueError):
        stroke_px = 0
    stroke = round(stroke_px * 288 / 1080)

    background = style.get("background") in (True, "true", "1", 1, "on", "yes")

    def style_line(name, primary_colour, secondary_colour, outline_colour, border_style, outline, shadow):
        return (
            f"Style: {name},{font_name},{size},{primary_colour},{secondary_colour},"
            f"{outline_colour},&H00000000,0,0,0,0,100,100,0,0,"
            f"{border_style},{outline},{shadow},{alignment},{margin_l},{margin_r},{margin_v},1"
        )

    output = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 384",
        "PlayResY: 288",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line("BG", "&HFF000000", "&HFF000000", "&H29000000", 3, 2, 0),
        style_line("Stroke", "&H000000", "&H000000", "&H000000", 1, stroke, 0),
        style_line("Fill", primary, secondary, "&H000000", 1, 0, 0),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for start, end, text in parse_srt_segments(srt_content):
        start_ts = ass_timestamp(start)
        end_ts = ass_timestamp(end)
        full_text = ass_escape_text(text)
        if background:
            output.append(f"Dialogue: 0,{start_ts},{end_ts},BG,,0,0,0,,{full_text}")
        if stroke > 0:
            output.append(f"Dialogue: 1,{start_ts},{end_ts},Stroke,,0,0,0,,{full_text}")
        output.append(
            f"Dialogue: 2,{start_ts},{end_ts},Fill,,0,0,0,,{karaoke_timed_text(text, start, end)}"
        )

    return "\n".join(output) + "\n"


def build_export_command(upload_path, srt_path, output_path, style):
    font_key = style.get("font", "sans")
    font_name = ASS_FONT_MAP.get(font_key, "DejaVu Sans")

    # ASS FontSize is relative to the default PlayResY=288 used by ffmpeg's
    # subtitles filter, so FontSize=11 renders ~3.8% of the video height.
    # This matches the preview caption ratio (SIZE_RATIO in tools/index.html).
    try:
        ratio = max(0.7, min(2.0, float(style.get("font_size", 1) or 1)))
    except (TypeError, ValueError):
        ratio = 1.0
    size = max(6, min(40, round(11 * ratio)))

    color = ass_color(style.get("color", "#ffffff"))
    alignment, margin_l, margin_r, margin_v = caption_layout(style)

    # "Trazo" comes as pixels at a 1080p reference (like Premiere) and is
    # converted to ASS units (PlayResY=288) so it scales with the video height.
    try:
        stroke_px = max(0, min(40, int(float(style.get("outline", 0) or 0))))
    except (TypeError, ValueError):
        stroke_px = 0
    stroke = round(stroke_px * 288 / 1080)

    background = style.get("background") in (True, "true", "1", 1, "on", "yes")

    # The letter must always render on top. Layers are stacked as chained
    # subtitles filters so the box/stroke are drawn underneath the fill.
    layers = []

    if background:
        layers.append(
            f"FontName={font_name},FontSize={size},PrimaryColour=&HFF000000,"
            f"OutlineColour=&H29000000,BorderStyle=3,Outline=2,Shadow=0,"
            f"Alignment={alignment},MarginL={margin_l},MarginR={margin_r},MarginV={margin_v}"
        )

    if stroke > 0:
        layers.append(
            f"FontName={font_name},FontSize={size},PrimaryColour=&H000000,"
            f"OutlineColour=&H000000,BorderStyle=1,Outline={stroke},Shadow=0,"
            f"Alignment={alignment},MarginL={margin_l},MarginR={margin_r},MarginV={margin_v}"
        )

    layers.append(
        f"FontName={font_name},FontSize={size},PrimaryColour={color},"
        f"OutlineColour=&H000000,BorderStyle=1,Outline=0,Shadow=0,"
        f"Alignment={alignment},MarginL={margin_l},MarginR={margin_r},MarginV={margin_v}"
    )

    video_filter = ",".join(
        f"subtitles=filename={srt_path}:fontsdir={FONTS_DIR}:force_style='{style_str}'"
        for style_str in layers
    )

    return [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(upload_path),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_ass_export_command(upload_path, ass_path, output_path):
    return [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(upload_path),
        "-vf",
        f"subtitles=filename={ass_path}:fontsdir={FONTS_DIR}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def append_log(job, line):
    with JOBS_LOCK:
        job["log"].append(line.rstrip())
        job["log"] = job["log"][-300:]


def run_job(job_id):
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        command = job["command"]
        started_at = job["started_at"]

    env = os.environ.copy()
    cuda_path = cuda_library_path()
    if cuda_path:
        env["LD_LIBRARY_PATH"] = (
            cuda_path
            if not env.get("LD_LIBRARY_PATH")
            else f"{cuda_path}:{env['LD_LIBRARY_PATH']}"
        )

    append_log(job, "$ " + " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    with process.stdout:
        for line in process.stdout:
            append_log(job, line)

    return_code = process.wait()
    stem = Path(job["upload_name"]).stem
    output_paths = [
        path
        for path in OUTPUT_DIR.glob(f"{stem}.*")
        if path.is_file() and path.stat().st_mtime >= started_at - 2
    ]
    if return_code == 0:
        spaces = int_option(job.get("word_spacing"), 1, 1, 12)
        max_words = int_option(job.get("max_words_per_subtitle"), 0, 0, 20)
        for path in output_paths:
            try:
                enforce_max_words_per_subtitle(path, max_words)
                apply_word_spacing(path, spaces)
                stats = subtitle_word_stats(path)
                if stats and max_words:
                    append_log(
                        job,
                        (
                            f"Verificado {path.name}: {stats['blocks']} subtítulos, "
                            f"máximo {stats['max_words']} palabras por subtítulo."
                        ),
                    )
            except UnicodeDecodeError:
                append_log(job, f"No se pudo aplicar espaciado a {path.name}: encoding no compatible.")
    outputs = [path.name for path in output_paths]

    with JOBS_LOCK:
        job["return_code"] = return_code
        job["status"] = "done" if return_code == 0 else "error"
        job["outputs"] = sorted(outputs)


def run_export_job(job_id):
    with JOBS_LOCK:
        job = JOBS[job_id]
        command = job["command"]
        subtitle_path = Path(job["subtitle_path"])

    env = os.environ.copy()
    append_log(job, "$ " + " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    with process.stdout:
        for line in process.stdout:
            append_log(job, line)

    return_code = process.wait()
    try:
        subtitle_path.unlink(missing_ok=True)
    except OSError:
        pass

    output_name = job["output_name"]
    with JOBS_LOCK:
        job["return_code"] = return_code
        job["status"] = "done" if return_code == 0 else "error"
        job["outputs"] = [output_name] if return_code == 0 else []


def load_index_html():
    index_path = ROOT / "tools" / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return INDEX_HTML


class SubtitleHandler(BaseHTTPRequestHandler):
    server_version = "SubtitleUI/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(load_index_html())
            return

        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = dict(job) if job else None
            if not payload:
                self.send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                return
            payload.pop("command", None)
            self.send_json(payload)
            return

        if parsed.path.startswith("/uploads/"):
            filename = safe_filename(unquote(parsed.path.removeprefix("/uploads/")))
            path = UPLOAD_DIR / filename
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_file(path)
            return

        if parsed.path.startswith("/fonts/"):
            filename = safe_filename(unquote(parsed.path.removeprefix("/fonts/")))
            path = FONTS_DIR / filename
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_file(path)
            return

        if parsed.path.startswith("/api/outputs/"):
            filename = safe_filename(unquote(parsed.path.removeprefix("/api/outputs/")))
            path = OUTPUT_DIR / filename
            if not path.exists() or not path.is_file():
                self.send_json({"error": "Archivo no encontrado"}, HTTPStatus.NOT_FOUND)
                return
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="latin-1")
            self.send_json({"name": path.name, "content": content})
            return

        if parsed.path.startswith("/outputs/"):
            filename = safe_filename(unquote(parsed.path.removeprefix("/outputs/")))
            path = OUTPUT_DIR / filename
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{html.escape(filename)}"'
            )
            self.end_headers()
            with path.open("rb") as file_obj:
                shutil.copyfileobj(file_obj, self.wfile)
            return

        if parsed.path.startswith("/exports/"):
            filename = safe_filename(unquote(parsed.path.removeprefix("/exports/")))
            path = EXPORT_DIR / filename
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{html.escape(filename)}"'
            )
            self.end_headers()
            with path.open("rb") as file_obj:
                try:
                    shutil.copyfileobj(file_obj, self.wfile)
                except (ConnectionResetError, BrokenPipeError):
                    pass
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path == "/api/cleanup":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                job_id = payload.get("job_id", "")
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if not job or not job.get("srt_only") or job.get("status") != "done":
                        self.send_json({"error": "Tarea no válida"}, HTTPStatus.BAD_REQUEST)
                        return
                    upload_name = job.get("upload_name", "")
                    output_names = list(job.get("outputs", []))
                for name in [upload_name, *output_names]:
                    if name and Path(name).name == name:
                        for directory in (UPLOAD_DIR, OUTPUT_DIR):
                            try:
                                (directory / name).unlink(missing_ok=True)
                            except OSError:
                                pass
                self.send_json({"ok": True})
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Solicitud no válida"}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/export":
            self.handle_export()
            return
        if self.path == "/api/generate-seo":
            self.handle_generate_seo()
            return

        if self.path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not WHISPER_BIN.exists():
            self.send_json(
                {"error": f"No existe {WHISPER_BIN}. Instala whisper-ctranslate2."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

        video_field = form["video"] if "video" in form else None
        if video_field is None or not getattr(video_field, "filename", ""):
            self.send_json({"error": "Selecciona un archivo de video."}, HTTPStatus.BAD_REQUEST)
            return

        UPLOAD_DIR.mkdir(exist_ok=True)
        OUTPUT_DIR.mkdir(exist_ok=True)

        original_name = safe_filename(video_field.filename)
        upload_name = f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{original_name}"
        upload_path = UPLOAD_DIR / upload_name
        with upload_path.open("wb") as output:
            shutil.copyfileobj(video_field.file, output)

        options = {
            key: form.getfirst(key, "")
            for key in [
                "model",
                "device",
                "compute_type",
                "task",
                "language",
                "output_format",
                "word_timestamps",
                "split_mode",
                "max_words_per_line",
                "word_spacing",
                "max_line_width",
                "max_line_count",
                "beam_size",
                "batch_size",
                "vad_filter",
                "initial_prompt",
            ]
        }
        srt_only = form.getfirst("srt_only", "false") in ("true", "on", "1", "yes")
        if srt_only:
            options["output_format"] = "srt"

        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "queued",
            "log": [],
            "outputs": [],
            "return_code": None,
            "upload_name": upload_name,
            "original_name": original_name,
            "srt_only": srt_only,
            "started_at": time.time(),
            "command": make_command(upload_path, options),
            "word_spacing": options.get("word_spacing", "1"),
            "max_words_per_subtitle": (
                options.get("max_words_per_line", "0")
                if options.get("split_mode", "words") == "words"
                else "0"
            ),
        }
        with JOBS_LOCK:
            JOBS[job_id] = job

        thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
        thread.start()
        self.send_json({"id": job_id, "upload_name": upload_name})

    def handle_export(self):
        if not FFMPEG_BIN:
            self.send_json(
                {"error": "ffmpeg no está instalado en el sistema."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_json({"error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            return

        upload_name = safe_filename(payload.get("upload_name", ""))
        srt_content = payload.get("srt", "")
        style = payload.get("style") or {}

        upload_path = UPLOAD_DIR / upload_name
        if not upload_path.exists() or not upload_path.is_file():
            self.send_json({"error": "Video no encontrado."}, HTTPStatus.NOT_FOUND)
            return
        if not srt_content.strip():
            self.send_json(
                {"error": "No hay subtítulos para incrustar."}, HTTPStatus.BAD_REQUEST
            )
            return

        EXPORT_DIR.mkdir(exist_ok=True)
        job_id = uuid.uuid4().hex

        output_name = f"{upload_path.stem}-subtitulado.mp4"
        output_path = EXPORT_DIR / output_name
        if output_path.exists():
            output_path.unlink()

        word_highlight = style.get("word_highlight") in (True, "true", "1", 1, "on", "yes")
        if word_highlight:
            subtitle_path = EXPORT_DIR / f"{job_id}.ass"
            subtitle_path.write_text(build_karaoke_ass(srt_content, style), encoding="utf-8")
            command = build_ass_export_command(upload_path, subtitle_path, output_path)
        else:
            subtitle_path = EXPORT_DIR / f"{job_id}.srt"
            subtitle_path.write_text(srt_content, encoding="utf-8")
            command = build_export_command(upload_path, subtitle_path, output_path, style)

        job = {
            "id": job_id,
            "status": "queued",
            "log": [],
            "outputs": [],
            "return_code": None,
            "upload_name": upload_name,
            "original_name": output_name,
            "started_at": time.time(),
            "command": command,
            "subtitle_path": str(subtitle_path),
            "output_name": output_name,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job

        thread = threading.Thread(target=run_export_job, args=(job_id,), daemon=True)
        thread.start()
        self.send_json({"id": job_id, "output_name": output_name})

    def handle_generate_seo(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_json({"error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            return

        srt_content = payload.get("srt", "")
        if not srt_content.strip():
            self.send_json({"error": "No hay subtítulos."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            seo_result = generate_youtube_seo_with_gemini(srt_content)
            self.send_json({"result": seo_result})
        except Exception as e:
            self.send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def send_html(self, body):
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, payload, status=HTTPStatus.OK):
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_file(self, path):
        file_size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range")

        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                start_str, end_str = match.groups()
                start = int(start_str) if start_str else 0
                if end_str:
                    end = min(int(end_str), file_size - 1)
                else:
                    end = file_size - 1

                if start > end or start >= file_size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return

                length = end - start + 1
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with path.open("rb") as file_obj:
                    file_obj.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = file_obj.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (ConnectionResetError, BrokenPipeError):
                            return
                        remaining -= len(chunk)
                return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()
        with path.open("rb") as file_obj:
            try:
                shutil.copyfileobj(file_obj, self.wfile)
            except (ConnectionResetError, BrokenPipeError):
                pass


INDEX_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Subtítulos</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #20242c;
      --muted: #687080;
      --line: #d9dee7;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --warn: #9a3412;
      --mono: #111827;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 24px auto;
      display: grid;
      grid-template-columns: minmax(320px, 420px) 1fr;
      gap: 18px;
      align-items: start;
    }
    h1 {
      margin: 0 0 14px;
      font-size: 26px;
      line-height: 1.15;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 16px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(20, 30, 40, .05);
    }
    .stack { display: grid; gap: 14px; }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }
    input, select, textarea {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--ink);
      background: #fff;
      font: inherit;
      font-size: 14px;
    }
    textarea { min-height: 76px; resize: vertical; }
    input[type="checkbox"] {
      width: 18px;
      min-height: 18px;
      accent-color: var(--accent);
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 9px;
      color: var(--ink);
      font-size: 14px;
    }
    .hint {
      margin: -4px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    button {
      min-height: 42px;
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      font-weight: 750;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button:disabled { opacity: .6; cursor: wait; }
    .status {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--line);
    }
    .badge {
      min-width: 92px;
      text-align: center;
      border-radius: 999px;
      padding: 5px 10px;
      background: #e7f6f3;
      color: var(--accent-dark);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }
    pre {
      min-height: 420px;
      max-height: calc(100vh - 220px);
      margin: 14px 0 0;
      overflow: auto;
      border-radius: 6px;
      background: #10151f;
      color: #d8e1f0;
      padding: 14px;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }
    .downloads {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .downloads a {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--accent-dark);
      background: #fff;
      text-decoration: none;
      font-size: 14px;
      font-weight: 700;
    }
    .warning { color: var(--warn); }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; width: min(100vw - 20px, 680px); margin-top: 10px; }
      .row { grid-template-columns: 1fr; }
      pre { min-height: 300px; max-height: 480px; }
    }
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>Subtítulos</h1>
      <form id="jobForm" class="stack">
        <label>Video
          <input name="video" type="file" accept="video/*,audio/*" required>
        </label>

        <div class="row">
          <label>Modelo
            <select name="model">
              <option value="large-v3" selected>large-v3</option>
              <option value="large-v3-turbo">large-v3-turbo</option>
              <option value="turbo">turbo</option>
              <option value="medium">medium</option>
              <option value="small">small</option>
              <option value="base">base</option>
            </select>
          </label>
          <label>Idioma
            <select name="language">
              <option value="es" selected>Español</option>
              <option value="en">Inglés</option>
              <option value="auto">Auto</option>
            </select>
          </label>
        </div>

        <div class="row">
          <label>Dispositivo
            <select name="device">
              <option value="cuda" selected>CUDA</option>
              <option value="cpu">CPU</option>
              <option value="auto">Auto</option>
            </select>
          </label>
          <label>Precisión
            <select name="compute_type">
              <option value="float16" selected>float16</option>
              <option value="int8_float16">int8_float16</option>
              <option value="int8">int8</option>
              <option value="float32">float32</option>
            </select>
          </label>
        </div>

        <label>Acción
          <select name="task" id="task">
            <option value="transcribe" selected>Transcribir en el idioma original</option>
            <option value="translate">Traducir a inglés</option>
          </select>
        </label>
        <p class="hint">La traducción usa Whisper y genera el archivo elegido, por ejemplo SRT, con los mismos tiempos.</p>

        <div class="row">
          <label>Formato
            <select name="output_format">
              <option value="srt" selected>SRT</option>
              <option value="vtt">VTT</option>
              <option value="txt">TXT</option>
              <option value="all">Todos</option>
            </select>
          </label>
          <label>Beam size
            <input name="beam_size" type="number" min="1" max="10" value="5">
          </label>
        </div>

        <label>Separación
          <select name="split_mode" id="splitMode">
            <option value="words" selected>Por palabras</option>
            <option value="width">Por ancho de línea</option>
            <option value="none">Sin ajuste</option>
          </select>
        </label>

        <div id="wordsPanel">
          <label>Máximo de palabras por subtítulo
            <input name="max_words_per_line" type="number" min="1" max="20" value="3">
          </label>
          <p class="hint">Para separación fuerte, usa 1, 2 o 3.</p>
        </div>

        <label>Espacios entre palabras
          <input name="word_spacing" type="number" min="1" max="12" value="1">
        </label>
        <p class="hint">1 mantiene el texto normal. Usa 2 o más para abrir el espaciado visual.</p>

        <div id="widthPanel" hidden>
          <div class="row">
            <label>Ancho máximo
              <input name="max_line_width" type="number" min="10" max="80" value="42">
            </label>
            <label>Líneas máximas
              <input name="max_line_count" type="number" min="1" max="4" value="2">
            </label>
          </div>
        </div>

        <label class="check">
          <input name="word_timestamps" type="checkbox" value="true" checked>
          Timestamps por palabra
        </label>
        <p class="hint">Se activan automáticamente cuando separas por palabras o ancho.</p>

        <label class="check">
          <input name="vad_filter" type="checkbox" value="true">
          Filtrar silencios
        </label>

        <label>Batch size
          <input name="batch_size" type="number" min="1" max="32" placeholder="Opcional">
        </label>

        <label>Prompt inicial
          <textarea name="initial_prompt" placeholder="Opcional"></textarea>
        </label>

        <button id="submitButton" type="submit">Transcribir</button>
      </form>
    </section>

    <section class="panel">
      <div class="status">
        <h2>Estado</h2>
        <span id="statusBadge" class="badge">Listo</span>
      </div>
      <pre id="log">Esperando archivo...</pre>
      <div id="downloads" class="downloads"></div>
    </section>
  </main>

  <script>
    const form = document.querySelector("#jobForm");
    const task = document.querySelector("#task");
    const splitMode = document.querySelector("#splitMode");
    const wordsPanel = document.querySelector("#wordsPanel");
    const widthPanel = document.querySelector("#widthPanel");
    const button = document.querySelector("#submitButton");
    const badge = document.querySelector("#statusBadge");
    const log = document.querySelector("#log");
    const downloads = document.querySelector("#downloads");
    let timer = null;

    function syncSplitMode() {
      wordsPanel.hidden = splitMode.value !== "words";
      widthPanel.hidden = splitMode.value !== "width";
      const requiresWordTimestamps = splitMode.value === "words" || splitMode.value === "width";
      form.word_timestamps.checked = requiresWordTimestamps || form.word_timestamps.checked;
      form.word_timestamps.disabled = requiresWordTimestamps;
    }

    splitMode.addEventListener("change", syncSplitMode);
    syncSplitMode();

    function syncTask() {
      button.textContent = task.value === "translate" ? "Traducir" : "Transcribir";
    }

    task.addEventListener("change", syncTask);
    syncTask();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      badge.textContent = "Subiendo";
      log.textContent = "Subiendo archivo...";
      downloads.innerHTML = "";

      const data = new FormData(form);
      if (data.get("language") === "auto") data.delete("language");
      if (!form.word_timestamps.checked) data.set("word_timestamps", "false");
      if (!form.vad_filter.checked) data.delete("vad_filter");

      const response = await fetch("/api/jobs", { method: "POST", body: data });
      const payload = await response.json();
      if (!response.ok) {
        badge.textContent = "Error";
        log.textContent = payload.error || "No se pudo crear la tarea.";
        button.disabled = false;
        return;
      }
      poll(payload.id);
    });

    async function poll(id) {
      clearTimeout(timer);
      const response = await fetch(`/api/jobs/${id}`);
      const job = await response.json();
      badge.textContent = job.status || "Error";
      log.textContent = (job.log || []).join("\n") || "Preparando...";

      console.log("Job outputs:", job.outputs); // Debugging

      downloads.innerHTML = "";
      for (const name of job.outputs || []) {
        const link = document.createElement("a");
        link.href = `/outputs/${encodeURIComponent(name)}`;
        link.textContent = name;
        downloads.appendChild(link);
      }

      // Add Generate SEO button
      const srtFile = (job.outputs || []).find(f => f.endsWith(".srt"));
      console.log("srtFile found:", srtFile); // Debugging
      if (srtFile && job.status === "done") {
        const seoButton = document.createElement("button");
        seoButton.textContent = "✨ Generar SEO & Capítulos";
        seoButton.onclick = async () => {
          seoButton.disabled = true;
          seoButton.textContent = "Generando...";
          const srtResponse = await fetch(`/api/outputs/${srtFile}`);
          const srtPayload = await srtResponse.json();
          const srt = srtPayload.content;
          const response = await fetch("/api/generate-seo", { 
            method: "POST", 
            body: JSON.stringify({ srt }) 
          });
          const payload = await response.json();
          if (response.ok) {
            const seoPre = document.createElement("pre");
            seoPre.textContent = payload.result;
            seoPre.style.marginTop = "10px";
            downloads.appendChild(seoPre);
          } else {
            alert("Error: " + (payload.error || "Algo salió mal"));
          }
          seoButton.disabled = false;
          seoButton.textContent = "✨ Generar SEO & Capítulos";
        };
        downloads.appendChild(seoButton);
      }

      if (job.status === "done" || job.status === "error") {
        button.disabled = false;
        if (job.status === "error") badge.classList.add("warning");
        return;
      }
      timer = setTimeout(() => poll(id), 1500);
    }
  </script>
</body>
</html>
"""


def main():
    port = int(os.environ.get("SUBTITLE_UI_PORT", "8765"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), SubtitleHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"El puerto {port} ya esta en uso.")
            print("Abre http://127.0.0.1:8765 si la interfaz ya esta corriendo,")
            print("o usa otro puerto asi: SUBTITLE_UI_PORT=8766 python tools/subtitle_ui.py")
            return
        raise
    print(f"Subtitle UI: http://127.0.0.1:{port}")
    print("Presiona Ctrl+C para cerrar.")
    server.serve_forever()


if __name__ == "__main__":
    main()
