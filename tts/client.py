import logging
import time

import requests

from .config import TTSConfig
from .models import TTSAPIError, TTSConfigurationError, TTSRequest

logger = logging.getLogger(__name__)


class YiZhanTTSClient:
    def __init__(self, config: TTSConfig | None = None, session: requests.Session | None = None) -> None:
        self.config = config or TTSConfig.from_env()
        self.session = session or requests.Session()

    def synthesize_segment(self, request: TTSRequest) -> bytes:
        if not self.config.api_key:
            raise TTSConfigurationError("YIZHAN_API_KEY is not configured; set it in .env or environment variables.")

        payload = {
            "model": request.model,
            "input": request.text,
            "voice": request.voice,
        }
        if request.response_format and request.response_format != "mp3":
            payload["response_format"] = request.response_format
        if request.speed != 1.0:
            payload["speed"] = request.speed
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        attempts = max(1, self.config.max_retries)
        for attempt in range(1, attempts + 1):
            try:
                logger.info(
                    "Calling YiZhan TTS model=%s voice=%s format=%s chars=%s attempt=%s",
                    request.model,
                    request.voice,
                    request.response_format,
                    len(request.text),
                    attempt,
                )
                response = self.session.post(
                    self.config.speech_url,
                    json=payload,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
            except requests.Timeout as exc:
                if attempt >= attempts:
                    raise TTSAPIError("YiZhan TTS request timed out", error_summary=str(exc)) from exc
                self._sleep_before_retry(attempt)
                continue
            except requests.RequestException as exc:
                if attempt >= attempts:
                    raise TTSAPIError("YiZhan TTS network request failed", error_summary=str(exc)) from exc
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == 200:
                content = self._audio_bytes_from_response(response)
                if len(content) < 128:
                    raise TTSAPIError(
                        "YiZhan TTS returned empty or too-small audio",
                        status_code=response.status_code,
                        request_id=self._request_id(response),
                    )
                return content

            if not self._should_retry(response.status_code) or attempt >= attempts:
                raise TTSAPIError(
                    "YiZhan TTS request failed",
                    status_code=response.status_code,
                    error_summary=self._error_summary(response),
                    request_id=self._request_id(response),
                )
            self._sleep_before_retry(attempt)

        raise TTSAPIError("YiZhan TTS request failed after retries")

    def _audio_bytes_from_response(self, response: requests.Response) -> bytes:
        content_type = (response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return response.content or b""

        try:
            data = response.json()
        except ValueError as exc:
            raise TTSAPIError(
                "YiZhan TTS returned non-audio response",
                status_code=response.status_code,
                error_summary=(response.text or "")[:500],
                request_id=self._request_id(response),
            ) from exc

        audio = ((data.get("output") or {}).get("audio") or {})
        audio_data = audio.get("data")
        if audio_data:
            import base64

            return base64.b64decode(audio_data)

        audio_url = audio.get("url")
        if audio_url:
            logger.info("Downloading YiZhan TTS audio from returned URL")
            audio_response = self.session.get(audio_url, timeout=self.config.timeout_seconds)
            if audio_response.status_code != 200:
                raise TTSAPIError(
                    "YiZhan TTS audio URL download failed",
                    status_code=audio_response.status_code,
                    error_summary=(audio_response.text or "")[:500],
                    request_id=data.get("request_id"),
                )
            return audio_response.content or b""

        raise TTSAPIError(
            "YiZhan TTS JSON response did not include audio data or URL",
            status_code=response.status_code,
            error_summary=str(data)[:500],
            request_id=data.get("request_id"),
        )

    @staticmethod
    def _should_retry(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    @staticmethod
    def _request_id(response: requests.Response) -> str | None:
        return (
            response.headers.get("x-request-id")
            or response.headers.get("x-trace-id")
            or response.headers.get("request-id")
        )

    @staticmethod
    def _error_summary(response: requests.Response) -> str:
        text = (response.text or "").strip()
        return text[:500]

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        time.sleep(min(2 ** (attempt - 1), 8))
