import os
import subprocess
import tempfile
from pathlib import Path


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


def _concat_list_line(path: str) -> str:
    normalized = Path(path).resolve().as_posix().replace("'", "'\\''")
    return f"file '{normalized}'\n"


def try_fast_concat(video_paths: list[str], output_path: str) -> bool:
    if not video_paths:
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
        list_path = f.name
        for path in video_paths:
            f.write(_concat_list_line(path))

    try:
        cmd = [
            _ffmpeg_exe(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0 and os.path.isfile(output_path)
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def moviepy_concat(video_paths: list[str], output_path: str) -> None:
    VideoFileClip, concatenate_videoclips = _moviepy_symbols(
        "VideoFileClip",
        "concatenate_videoclips",
    )

    clips: list = []
    final_clip = None
    try:
        for path in video_paths:
            clips.append(VideoFileClip(path))
        final_clip = concatenate_videoclips(clips, method="compose")
        try:
            final_clip.write_videofile(
                output_path,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
        except TypeError:
            final_clip.write_videofile(
                output_path,
                codec="libx264",
                audio_codec="aac",
            )
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


def concat_videos(video_paths: list[str], output_path: str, prefer_fast: bool = True) -> str:
    if prefer_fast and try_fast_concat(video_paths, output_path):
        return "ffmpeg-copy"
    moviepy_concat(video_paths, output_path)
    return "moviepy"
