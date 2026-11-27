import importlib.util
from pathlib import Path


def load_subtitle_ui():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "subtitle_ui.py"
    spec = importlib.util.spec_from_file_location("subtitle_ui", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subtitle_text_blocks(content):
    blocks = []
    for block in content.strip().split("\n\n"):
        lines = block.splitlines()
        text = " ".join(line for line in lines if "-->" not in line and not line.isdigit())
        blocks.append(text)
    return blocks


def test_make_command_supports_translate_task(tmp_path):
    subtitle_ui = load_subtitle_ui()
    video_path = tmp_path / "sample.mp4"

    command = subtitle_ui.make_command(
        video_path,
        {
            "task": "translate",
            "language": "es",
            "output_format": "srt",
            "split_mode": "none",
        },
    )

    assert command[command.index("--task") + 1] == "translate"
    assert command[command.index("--language") + 1] == "es"
    assert command[command.index("--output_format") + 1] == "srt"


def test_enforce_max_words_per_subtitle_splits_long_srt_block(tmp_path):
    subtitle_ui = load_subtitle_ui()
    path = tmp_path / "sample.srt"
    path.write_text(
        "\n".join(
            [
                "1",
                "00:01:03,280 --> 00:01:08,240",
                "A pesar de que múltiples testimonios pusieron en duda su participación directa en los hechos,",
                "",
            ]
        ),
        encoding="utf-8",
    )

    subtitle_ui.enforce_max_words_per_subtitle(path, 3)

    content = path.read_text(encoding="utf-8")
    blocks = subtitle_text_blocks(content)
    assert len(blocks) == 5
    assert all(len(block.split()) <= 3 for block in blocks)
    assert "00:01:03,280 -->" in content
    assert "--> 00:01:08,240" in content


def test_enforce_max_words_per_subtitle_ignores_txt(tmp_path):
    subtitle_ui = load_subtitle_ui()
    path = tmp_path / "sample.txt"
    path.write_text("uno dos tres cuatro", encoding="utf-8")

    subtitle_ui.enforce_max_words_per_subtitle(path, 2)

    assert path.read_text(encoding="utf-8") == "uno dos tres cuatro"
