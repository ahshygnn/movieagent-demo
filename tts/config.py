import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def normalize_openai_base_url(value: str) -> str:
    base = (value or "https://vip.yi-zhan.top/v1/").strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/"


@dataclass(frozen=True)
class TTSConfig:
    api_key: str
    base_url: str = "https://vip.yi-zhan.top/v1/"
    model: str = "qwen3-tts-flash"
    default_voice: str = "Cherry"
    timeout_seconds: float = 120.0
    max_retries: int = 3
    max_segment_chars: int = 300

    @property
    def speech_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/audio/speech"

    @classmethod
    def from_env(cls) -> "TTSConfig":
        return cls(
            api_key=os.getenv("YIZHAN_API_KEY", "").strip(),
            base_url=normalize_openai_base_url(os.getenv("YIZHAN_BASE_URL", "https://vip.yi-zhan.top/v1/")),
            model=os.getenv("YIZHAN_TTS_MODEL", "qwen3-tts-flash").strip(),
            default_voice=os.getenv("YIZHAN_TTS_DEFAULT_VOICE", "Cherry").strip() or "Cherry",
            timeout_seconds=float(os.getenv("YIZHAN_TTS_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("YIZHAN_TTS_MAX_RETRIES", "3")),
            max_segment_chars=int(os.getenv("YIZHAN_TTS_MAX_SEGMENT_CHARS", "300")),
        )
