import re
import subprocess
from pathlib import Path

import config
from generation.postprocess import srt_timestamp, vtt_timestamp, write_srt, write_vtt


def _moviepy_symbols(*names: str):
    try:
        import moviepy

        return [getattr(moviepy, name) for name in names]
    except (ImportError, AttributeError):
        from moviepy import editor

        return [getattr(editor, name) for name in names]


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _filter_path(path: str) -> str:
    normalized = Path(path).resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", normalized):
        normalized = f"{normalized[0]}\\:{normalized[2:]}"
    return (
        normalized.replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def media_duration(path: str) -> float:
    (VideoFileClip,) = _moviepy_symbols("VideoFileClip")
    clip = VideoFileClip(path)
    try:
        return float(clip.duration or 0)
    finally:
        clip.close()


def parse_timestamp(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value.strip())
    if not match:
        raise ValueError(f"Invalid subtitle timestamp: {value}")
    hours, minutes, seconds, millis = [int(part) for part in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(path: str) -> list[tuple[float, float, str]]:
    content = Path(path).read_text(encoding="utf-8-sig").strip()
    if not content:
        return []
    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timing_line = lines[1] if lines[0].isdigit() else lines[0]
        text_lines = lines[2:] if lines[0].isdigit() else lines[1:]
        if "-->" not in timing_line:
            continue
        start_text, end_text = [part.strip() for part in timing_line.split("-->", 1)]
        entries.append((parse_timestamp(start_text), parse_timestamp(end_text), "\n".join(text_lines)))
    return entries


def offset_entries(entries: list[tuple[float, float, str]], offset: float) -> list[tuple[float, float, str]]:
    return [(start + offset, end + offset, text) for start, end, text in entries]


def merge_sidecar_subtitles(
    video_paths: list[str],
    subtitle_paths: list[str | None],
    output_srt_path: str,
    output_vtt_path: str,
) -> dict:
    merged: list[tuple[float, float, str]] = []
    offset = 0.0
    for video_path, subtitle_path in zip(video_paths, subtitle_paths):
        if subtitle_path and Path(subtitle_path).is_file():
            merged.extend(offset_entries(parse_srt(subtitle_path), offset))
        offset += media_duration(video_path)

    write_srt(merged, output_srt_path)
    write_vtt(merged, output_vtt_path)
    return {
        "subtitle_srt_local_path": output_srt_path,
        "subtitle_local_path": output_vtt_path,
        "entries": len(merged),
        "duration_seconds": offset,
    }


def subtitle_style() -> str:
    font = getattr(config, "SUBTITLE_FONT", "Microsoft YaHei")
    size = int(getattr(config, "SUBTITLE_FONT_SIZE", 24) or 24)
    return (
        f"FontName={font},FontSize={size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=32"
    )


def burn_subtitles(video_path: str, subtitle_path: str, output_path: str) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-i",
        video_path,
        "-vf",
        f"subtitles='{_filter_path(subtitle_path)}':force_style='{subtitle_style()}'",
        "-c:v",
        "libx264",
        "-c:a",
        "copy",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle burn failed: {result.stderr}")
    return output_path
