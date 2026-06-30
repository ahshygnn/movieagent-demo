import tempfile
import json
import unittest
from pathlib import Path
from unittest import mock

import requests

from tts.client import YiZhanTTSClient
from tts.config import TTSConfig
from tts.models import TTSAPIError, TTSConfigurationError, TTSRequest
from tts.service import TTSService, split_text


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text or self.content.decode("utf-8"))


class TTSServiceTests(unittest.TestCase):
    def test_missing_key_fails_before_request(self):
        session = mock.Mock()
        client = YiZhanTTSClient(TTSConfig(api_key=""), session=session)

        with self.assertRaises(TTSConfigurationError):
            client.synthesize_segment(TTSRequest(text="你好"))

        session.post.assert_not_called()

    def test_client_posts_openai_compatible_payload(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(200, content=b"x" * 1024, headers={"content-type": "audio/mpeg"})
        config = TTSConfig(api_key="secret", base_url="https://example.test/v1/", max_retries=1)
        client = YiZhanTTSClient(config, session=session)

        audio = client.synthesize_segment(TTSRequest(text="你好", voice="Cherry", speed=1.1))

        self.assertEqual(audio, b"x" * 1024)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs["json"]["model"], "qwen3-tts-flash")
        self.assertEqual(kwargs["json"]["input"], "你好")
        self.assertEqual(kwargs["json"]["voice"], "Cherry")
        self.assertNotIn("response_format", kwargs["json"])
        self.assertEqual(kwargs["json"]["speed"], 1.1)
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer "))

    def test_client_downloads_audio_url_from_json_response(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(
            200,
            text='{"output":{"audio":{"url":"https://audio.example/test.wav"}},"request_id":"req-1"}',
            content=b'{"output":{"audio":{"url":"https://audio.example/test.wav"}},"request_id":"req-1"}',
            headers={"content-type": "application/json"},
        )
        session.get.return_value = FakeResponse(200, content=b"w" * 1024, headers={"content-type": "audio/wav"})
        client = YiZhanTTSClient(TTSConfig(api_key="secret", max_retries=1), session=session)

        audio = client.synthesize_segment(TTSRequest(text="您好，我是测试语音。"))

        self.assertEqual(audio, b"w" * 1024)
        session.get.assert_called_once_with("https://audio.example/test.wav", timeout=120.0)

    def test_retries_429_then_succeeds(self):
        session = mock.Mock()
        session.post.side_effect = [
            FakeResponse(429, text="rate limited"),
            FakeResponse(200, content=b"x" * 1024, headers={"content-type": "audio/mpeg"}),
        ]
        client = YiZhanTTSClient(TTSConfig(api_key="secret", max_retries=2), session=session)

        with mock.patch("tts.client.time.sleep"):
            audio = client.synthesize_segment(TTSRequest(text="你好"))

        self.assertEqual(audio, b"x" * 1024)
        self.assertEqual(session.post.call_count, 2)

    def test_does_not_retry_401(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(401, text="unauthorized", headers={"x-request-id": "req-1"})
        client = YiZhanTTSClient(TTSConfig(api_key="secret", max_retries=3), session=session)

        with self.assertRaises(TTSAPIError) as ctx:
            client.synthesize_segment(TTSRequest(text="你好"))

        self.assertEqual(session.post.call_count, 1)
        self.assertIn("status=401", str(ctx.exception))
        self.assertIn("request_id=req-1", str(ctx.exception))

    def test_split_text_prefers_sentence_boundaries(self):
        chunks = split_text("第一句很短。第二句也很短！第三句结束。", max_chars=8)

        self.assertEqual(chunks, ["第一句很短。", "第二句也很短！", "第三句结束。"])

    def test_service_writes_single_segment_audio(self):
        client = mock.Mock()
        client.synthesize_segment.return_value = b"x" * 1024
        service = TTSService(client=client, config=TTSConfig(api_key="secret"))

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "hello.mp3"
            result = service.synthesize("欢迎来到未来城市。", output_path=str(output))

            self.assertEqual(result, str(output))
            self.assertEqual(output.read_bytes(), b"x" * 1024)

    def test_timeout_is_reported_cleanly(self):
        session = mock.Mock()
        session.post.side_effect = requests.Timeout("slow")
        client = YiZhanTTSClient(TTSConfig(api_key="secret", max_retries=1), session=session)

        with self.assertRaises(TTSAPIError) as ctx:
            client.synthesize_segment(TTSRequest(text="你好"))

        self.assertIn("timed out", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
