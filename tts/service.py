import argparse
import logging
import re
import uuid
from pathlib import Path

from .client import YiZhanTTSClient
from .config import TTSConfig
from .models import TTSRequest

logger = logging.getLogger(__name__)


class TTSService:
    def __init__(self, client: YiZhanTTSClient | None = None, config: TTSConfig | None = None) -> None:
        self.config = config or TTSConfig.from_env()
        self.client = client or YiZhanTTSClient(self.config)

    def synthesize(
        self,
        text: str,
        voice: str = "Cherry",
        output_path: str | None = None,
        response_format: str = "mp3",
        speed: float = 1.0,
        model: str | None = None,
    ) -> str:
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("text is required for TTS synthesis")

        selected_voice = voice or self.config.default_voice
        selected_model = model or self.config.model
        output = Path(output_path or self._default_output_path(response_format))
        output.parent.mkdir(parents=True, exist_ok=True)

        segments = split_text(clean_text, self.config.max_segment_chars)
        logger.info("Synthesizing TTS segments=%s voice=%s model=%s", len(segments), selected_voice, selected_model)

        if len(segments) == 1:
            audio = self.client.synthesize_segment(
                TTSRequest(
                    text=segments[0],
                    voice=selected_voice,
                    model=selected_model,
                    response_format=response_format,
                    speed=speed,
                )
            )
            output.write_bytes(audio)
            return str(output)

        segment_paths: list[Path] = []
        for index, segment in enumerate(segments, start=1):
            part_path = output.with_name(f"{output.stem}.part{index:03d}.{response_format}")
            audio = self.client.synthesize_segment(
                TTSRequest(
                    text=segment,
                    voice=selected_voice,
                    model=selected_model,
                    response_format=response_format,
                    speed=speed,
                )
            )
            part_path.write_bytes(audio)
            segment_paths.append(part_path)

        concatenate_audio_files(segment_paths, output)
        return str(output)

    @staticmethod
    def _default_output_path(response_format: str) -> str:
        return str(Path("outputs/audio") / f"tts_{uuid.uuid4().hex}.{response_format}")


def split_text(text: str, max_chars: int = 300) -> list[str]:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs or [clean]:
        sentences = [s.strip() for s in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", paragraph) if s.strip()]
        current = ""
        for sentence in sentences or [paragraph]:
            if len(sentence) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(_split_long_sentence(sentence, max_chars))
                continue
            candidate = f"{current}{sentence}" if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    parts = [p for p in re.split(r"(，|、|,)", sentence) if p]
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def concatenate_audio_files(segment_paths: list[Path], output_path: Path) -> None:
    if not segment_paths:
        raise ValueError("no audio segments to concatenate")
    if len(segment_paths) == 1:
        output_path.write_bytes(segment_paths[0].read_bytes())
        return

    try:
        import moviepy

        AudioFileClip = getattr(moviepy, "AudioFileClip")
        concatenate_audioclips = getattr(moviepy, "concatenate_audioclips")
    except (ImportError, AttributeError):
        from moviepy import editor

        AudioFileClip = editor.AudioFileClip
        concatenate_audioclips = editor.concatenate_audioclips

    clips = []
    final_clip = None
    try:
        clips = [AudioFileClip(str(path)) for path in segment_paths]
        final_clip = concatenate_audioclips(clips)
        final_clip.write_audiofile(str(output_path), logger=None)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Chinese speech with YiZhan TTS.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--voice", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--format", default="mp3", dest="response_format")
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    service = TTSService()
    output = service.synthesize(
        text=args.text,
        voice=args.voice or service.config.default_voice,
        output_path=args.output,
        response_format=args.response_format,
        speed=args.speed,
    )
    print(output)


if __name__ == "__main__":
    main()
