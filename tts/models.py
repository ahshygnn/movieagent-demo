from dataclasses import dataclass


@dataclass(frozen=True)
class TTSRequest:
    text: str
    voice: str = "Cherry"
    model: str = "qwen3-tts-flash"
    response_format: str = "mp3"
    speed: float = 1.0


class TTSError(RuntimeError):
    """Base error for TTS failures."""


class TTSConfigurationError(TTSError):
    """Raised when required TTS configuration is missing."""


class TTSAPIError(TTSError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_summary: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.error_summary = error_summary
        self.request_id = request_id
        parts = [message]
        if status_code is not None:
            parts.append(f"status={status_code}")
        if error_summary:
            parts.append(f"error={error_summary}")
        if request_id:
            parts.append(f"request_id={request_id}")
        super().__init__("; ".join(parts))
