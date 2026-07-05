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


def _has_audio(path: str) -> bool:
    result = subprocess.run([_ffmpeg_exe(), "-i", path], capture_output=True, text=True)
    return "Audio:" in result.stderr


def _ensure_audio_track(path: str, tmp_dir: str) -> tuple[str, bool]:
    """
    保证片段带音轨：若视频无音频流，补一条静音 aac 轨（画面 -c:v copy 不动，不影响流畅度）。
    返回 (可用路径, 是否为新建临时文件)。这样各片段音轨一致，快速拼接才不会丢声音。
    """
    if _has_audio(path):
        return path, False
    fd, out = tempfile.mkstemp(suffix=".mp4", dir=tmp_dir)
    os.close(fd)
    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-i",
        path,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        out,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.isfile(out):
        try:
            os.remove(out)
        except OSError:
            pass
        return path, False
    return out, True


def try_fast_concat(video_paths: list[str], output_path: str) -> bool:
    if not video_paths:
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="concat_norm_")
    temp_files: list[str] = []
    try:
        # 先把缺音轨的片段补上静音轨，保证所有片段音频流一致
        normalized_paths: list[str] = []
        for path in video_paths:
            usable, created = _ensure_audio_track(path, tmp_dir)
            normalized_paths.append(usable)
            if created:
                temp_files.append(usable)

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
            list_path = f.name
            for path in normalized_paths:
                f.write(_concat_list_line(path))

        try:
            # 画面 -c:v copy 保持流畅；音频统一重编码为 aac 44100 立体声，
            # 避免各段音频参数不一致导致接缝异常或丢音。
            cmd = [
                _ffmpeg_exe(),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "44100",
                "-ac",
                "2",
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
    finally:
        for tf in temp_files:
            try:
                os.remove(tf)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
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
