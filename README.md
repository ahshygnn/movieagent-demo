# MovieAgent Demo

MovieAgent turns a script into planned shots, keyframes, video clips, Chinese voiceover, sidecar subtitles, and a final stitched MP4.

## Setup

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Create `.env` from `.env.example` and set your keys:

```env
YIZHAN_API_KEY=
YIZHAN_BASE_URL=https://vip.yi-zhan.top/v1/
YIZHAN_TTS_MODEL=qwen3-tts-flash
YIZHAN_TTS_DEFAULT_VOICE=Cherry
```

Do not commit real API keys.

## Chinese TTS

The reusable TTS module lives in `tts/` and calls YiZhan API Pro's OpenAI-compatible `/audio/speech` endpoint. It defaults to:

- model: `qwen3-tts-flash`
- voice: `Cherry`
- format: `mp3`
- speed: `1.0`

Minimal Python usage:

```python
from tts import TTSService

service = TTSService()
path = service.synthesize(
    text="欢迎来到未来城市。",
    voice="Cherry",
    output_path="./outputs/audio/welcome.mp3",
)
print(path)
```

Command line:

```bash
python -m tts.service --text "欢迎来到未来城市。" --voice Cherry --output ./outputs/audio/welcome.mp3
```

Long text is split on Chinese paragraph and sentence boundaries before synthesis. Segments are generated separately and then concatenated.

## Errors

If `YIZHAN_API_KEY` is missing, the TTS module raises a clear configuration error before sending a request.

Network timeouts, HTTP `429`, and `5xx` responses are retried with exponential backoff. HTTP `401`, `403`, and invalid requests fail immediately with status code, server error summary, and request id when present.

Logs never print the API key.

## Subtitles

Generated subtitles are sidecar files:

- `outputs/subtitles/{shot_id}.vtt`
- `outputs/subtitles/{shot_id}.srt`

They are not burned into the video, so a frontend subtitle module can display, edit, disable, or replace them later.

## Tests

Run local tests without calling real APIs:

```bash
python -m unittest test_speed_mode.py test_postprocess.py tts.tests.test_tts_service
```
