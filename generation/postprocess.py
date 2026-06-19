import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

import requests

import config


PAUSE_SECONDS = 0.35


def _moviepy_symbols(*names: str):
    try:
        import moviepy

        return [getattr(moviepy, name) for name in names]
    except (ImportError, AttributeError):
        from moviepy import editor

        return [getattr(editor, name) for name in names]


def collect_subtitle_lines(subtitles: dict | None) -> list[tuple[str, str]]:
    """Return non-empty dialogue lines in the order produced by the shot agent."""
    if not isinstance(subtitles, dict):
        return []
    lines: list[tuple[str, str]] = []
    for speaker, text in subtitles.items():
        clean_text = str(text or "").strip()
        if clean_text:
            lines.append((str(speaker), clean_text))
    return lines


def srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def vtt_timestamp(seconds: float) -> str:
    return srt_timestamp(seconds).replace(",", ".")


def build_srt_entries(
    timed_lines: Iterable[tuple[str, str, float]],
    pause_seconds: float = PAUSE_SECONDS,
) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    for speaker, text, duration in timed_lines:
        safe_duration = max(float(duration), 0.1)
        start = cursor
        end = start + safe_duration
        label = f"{speaker}: {text}" if speaker else text
        entries.append((start, end, label))
        cursor = end + pause_seconds
    return entries


def write_srt(entries: Iterable[tuple[float, float, str]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, (start, end, text) in enumerate(entries, start=1):
        blocks.append(
            f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n"
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))


def write_vtt(entries: Iterable[tuple[float, float, str]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    blocks = ["WEBVTT\n"]
    for start, end, text in entries:
        blocks.append(f"{vtt_timestamp(start)} --> {vtt_timestamp(end)}\n{text}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "line"


def _voice_for_speaker(speaker: str, voice_refs: dict | None) -> str:
    refs = voice_refs or {}
    voice = refs.get(speaker) or getattr(config, "DEFAULT_TTS_VOICE", "")
    if not voice:
        raise ValueError(
            f"No voice configured for {speaker}. Set DEFAULT_TTS_VOICE in .env "
            "or upload a reference voice for this character."
        )
    return voice


def synthesize_speech(
    text: str,
    voice: str,
    output_path: str,
    model: str = "FunAudioLLM/CosyVoice2-0.5B",
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "stream": False,
    }
    resp = requests.post(
        f"{config.BASE_URL}/audio/speech",
        json=payload,
        headers=headers,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"TTS failed {resp.status_code}: {resp.text}")
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return output_path


def _audio_duration(path: str) -> float:
    (AudioFileClip,) = _moviepy_symbols("AudioFileClip")

    clip = AudioFileClip(path)
    try:
        return float(clip.duration or 0)
    finally:
        clip.close()


def _video_duration(path: str) -> float:
    (VideoFileClip,) = _moviepy_symbols("VideoFileClip")

    clip = VideoFileClip(path)
    try:
        return float(clip.duration or 0)
    finally:
        clip.close()


def _combine_audio(audio_paths: list[str], output_path: str, pause_seconds: float) -> str:
    AudioClip, AudioFileClip, concatenate_audioclips = _moviepy_symbols(
        "AudioClip",
        "AudioFileClip",
        "concatenate_audioclips",
    )

    clips = []
    final_clip = None
    try:
        for idx, path in enumerate(audio_paths):
            clips.append(AudioFileClip(path))
            if idx < len(audio_paths) - 1 and pause_seconds > 0:
                clips.append(
                    AudioClip(
                        lambda t: 0 * t,
                        duration=pause_seconds,
                        fps=44100,
                    )
                )
        final_clip = concatenate_audioclips(clips)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            final_clip.write_audiofile(
                output_path,
                fps=44100,
                codec="libmp3lame",
                logger=None,
            )
        except TypeError:
            final_clip.write_audiofile(
                output_path,
                fps=44100,
                codec="libmp3lame",
            )
        return output_path
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
        if final_clip is not None:
            try:
                final_clip.close()
            except Exception:
                pass


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


def _subtitle_style() -> str:
    font = getattr(config, "SUBTITLE_FONT", "Microsoft YaHei")
    size = int(getattr(config, "SUBTITLE_FONT_SIZE", 28) or 28)
    return (
        f"FontName={font},FontSize={size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=36"
    )


def burn_subtitles_and_audio(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_path: str,
) -> str:
    video_duration = _video_duration(video_path)
    audio_duration = _audio_duration(audio_path)
    extend_by = max(0.0, audio_duration - video_duration)

    vf_parts = []
    if extend_by > 0.05:
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={extend_by:.3f}")
    vf_parts.append(
        f"subtitles='{_filter_path(subtitle_path)}':force_style='{_subtitle_style()}'"
    )
    vf = ",".join(vf_parts)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle/audio mux failed: {result.stderr}")
    return output_path


def mux_audio_with_video(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    video_duration = _video_duration(video_path)
    audio_duration = _audio_duration(audio_path)
    extend_by = max(0.0, audio_duration - video_duration)

    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if extend_by > 0.05:
        cmd.extend(["-vf", f"tpad=stop_mode=clone:stop_duration={extend_by:.3f}"])
        cmd.extend(["-c:v", "libx264"])
    else:
        cmd.extend(["-c:v", "copy"])
    cmd.extend([
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        output_path,
    ])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio mux failed: {result.stderr}")
    return output_path


def postprocess_shot_video(
    shot_id: str,
    video_path: str,
    shot_data: dict,
    voice_refs: dict | None = None,
    pause_seconds: float = PAUSE_SECONDS,
) -> dict:
    lines = collect_subtitle_lines(shot_data.get("Subtitles"))
    if not lines:
        return {
            "local_path": video_path,
            "dubbed": False,
            "audio_files": {},
            "subtitle_local_path": None,
            "subtitle_srt_local_path": None,
            "combined_audio_local_path": None,
        }

    os.makedirs("outputs/audio", exist_ok=True)
    os.makedirs("outputs/subtitles", exist_ok=True)
    os.makedirs(config.VIDEO_DIR, exist_ok=True)

    audio_files: dict[str, str] = {}
    timed_lines: list[tuple[str, str, float]] = []
    ordered_audio_paths: list[str] = []

    for index, (speaker, text) in enumerate(lines, start=1):
        voice = _voice_for_speaker(speaker, voice_refs)
        safe_speaker = _safe_name(speaker)
        audio_path = os.path.join(
            "outputs/audio",
            f"{shot_id}_{index:02d}_{safe_speaker}.mp3",
        )
        synthesize_speech(text, voice, audio_path)
        duration = _audio_duration(audio_path)
        audio_files[f"{speaker}_{index}"] = audio_path
        ordered_audio_paths.append(audio_path)
        timed_lines.append((speaker, text, duration))

    subtitle_entries = build_srt_entries(timed_lines, pause_seconds)
    subtitle_srt_path = os.path.join("outputs/subtitles", f"{shot_id}.srt")
    subtitle_vtt_path = os.path.join("outputs/subtitles", f"{shot_id}.vtt")
    write_srt(subtitle_entries, subtitle_srt_path)
    write_vtt(subtitle_entries, subtitle_vtt_path)

    combined_audio_path = os.path.join("outputs/audio", f"{shot_id}_dialogue.mp3")
    _combine_audio(ordered_audio_paths, combined_audio_path, pause_seconds)

    output_path = os.path.join(config.VIDEO_DIR, f"{shot_id}_dubbed.mp4")
    mux_audio_with_video(video_path, combined_audio_path, output_path)

    return {
        "local_path": output_path,
        "dubbed": True,
        "audio_files": audio_files,
        "subtitle_local_path": subtitle_vtt_path,
        "subtitle_srt_local_path": subtitle_srt_path,
        "combined_audio_local_path": combined_audio_path,
    }
