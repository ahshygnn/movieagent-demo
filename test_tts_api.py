"""
test_tts_api.py
一展 OpenAI 兼容 TTS 接口手动测试脚本。

用法：
    python test_tts_api.py
    python test_tts_api.py --text "小松鼠奇奇找到了松果。" --voice Cherry
    python test_tts_api.py --model qwen3-tts-flash
    python test_tts_api.py --long          # 额外测试长文本分段合成
    python test_tts_api.py --raw           # 只测原始 HTTP 响应（不保存文件）
    python test_tts_api.py --list-voices   # 打印常用音色名

依赖 .env：
    YIZHAN_API_KEY=...
    YIZHAN_BASE_URL=https://vip.yi-zhan.top/v1/   # 可选
    YIZHAN_TTS_MODEL=qwen3-tts-flash              # 推荐；qwen-tts-2025-05-22 可能返回 500
    YIZHAN_TTS_DEFAULT_VOICE=Cherry               # 可选
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import config
from generation.postprocess import synthesize_speech
from tts.config import TTSConfig
from tts.models import TTSAPIError, TTSConfigurationError
from tts.service import TTSService, split_text

# ══════════════════════════════════════════════════════════════════
# 默认测试内容（可直接改这里）
# ══════════════════════════════════════════════════════════════════

DEFAULT_TEXT = "小松鼠奇奇住在松树上。今天，它要去远处的松林找松果。"
DEFAULT_VOICE = ""  # 留空则使用 .env 里的 YIZHAN_TTS_DEFAULT_VOICE
DEFAULT_MODEL = ""  # 留空则使用 .env 里的 YIZHAN_TTS_MODEL
DEFAULT_OUTPUT = ""  # 留空则自动生成到 outputs/audio/test_tts_*.mp3

LONG_TEXT = (
    "小兔子白白住在森林里。一天，它想去河对岸找胡萝卜，但桥断了。"
    "它用木头搭了一座小桥，成功过了河，带着胡萝卜开心回家。"
    "白白开心地说：只要肯动脑筋，就没有过不去的难关。"
)

COMMON_VOICES = ["Cherry", "Serena", "Ethan", "Chelsie"]


def _looks_like_audio(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return True
    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        return True
    return False


def _inspect_saved_file(path: str) -> list[str]:
    warnings: list[str] = []
    data = Path(path).read_bytes()
    if data[:1] == b"{" or data[:1] == b"[":
        warnings.append("输出文件是 JSON，不是音频二进制（接口可能返回了 URL 包装）")
        try:
            payload = json.loads(data.decode("utf-8"))
            url = (
                payload.get("output", {})
                .get("audio", {})
                .get("url")
            )
            if url:
                warnings.append(f"JSON 中包含音频 URL: {url}")
        except Exception:
            pass
    elif not _looks_like_audio(data):
        warnings.append("文件头不像常见 mp3/wav 格式，请手动播放确认")
    if len(data) < 10 * 1024:
        warnings.append(f"文件仅 {len(data)} 字节，可能过短")
    return warnings


def run_raw_request(text: str, voice: str, model: str, speed: float) -> int:
    cfg = print_config()
    if not cfg.api_key:
        print("\n错误：未配置 YIZHAN_API_KEY")
        return 1

    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "speed": speed,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    print("\n[raw]")
    print(f"  POST {cfg.speech_url}")
    print(f"  payload: {json.dumps(payload, ensure_ascii=False)}")

    t0 = time.time()
    try:
        resp = requests.post(
            cfg.speech_url,
            json=payload,
            headers=headers,
            timeout=cfg.timeout_seconds,
        )
    except requests.RequestException as exc:
        print(f"  status  : NETWORK ERROR ({time.time()-t0:.1f}s)")
        print(f"  error   : {exc}")
        return 1

    elapsed = time.time() - t0
    content = resp.content or b""
    preview = content[:200]
    print(f"  status  : {resp.status_code} ({elapsed:.1f}s)")
    print(f"  type    : {resp.headers.get('Content-Type', '(unknown)')}")
    print(f"  bytes   : {len(content)}")
    print(f"  preview : {preview!r}")

    if resp.status_code == 200 and not _looks_like_audio(content):
        print("  note    : 200 响应但不是标准音频头，可能是 JSON 包装或空 data 字段")

    return 0 if resp.status_code == 200 else 1


def _audio_duration(path: str) -> float | None:
    try:
        from moviepy import AudioFileClip

        clip = AudioFileClip(path)
        duration = float(clip.duration or 0)
        clip.close()
        return duration
    except Exception:
        return None


def _mask_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def print_config() -> TTSConfig:
    cfg = TTSConfig.from_env()
    print("=" * 60)
    print("TTS 配置")
    print("=" * 60)
    print(f"API Key      : {_mask_key(cfg.api_key)}")
    print(f"Base URL     : {cfg.base_url}")
    print(f"Speech URL   : {cfg.speech_url}")
    print(f"Model        : {cfg.model}")
    print(f"Default Voice: {cfg.default_voice}")
    print(f"Timeout      : {cfg.timeout_seconds}s")
    print(f"Max Retries  : {cfg.max_retries}")
    print(f"Max Segment  : {cfg.max_segment_chars} chars")
    print("=" * 60)
    return cfg


def _resolve_output(output: str | None, label: str) -> str:
    if output:
        return output
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"outputs/audio/test_tts_{label}_{ts}.mp3"


def run_case(
    *,
    label: str,
    text: str,
    voice: str,
    model: str | None,
    output: str | None,
    speed: float,
    use_postprocess: bool,
) -> dict:
    output_path = _resolve_output(output, label)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    segments = split_text(text, TTSConfig.from_env().max_segment_chars)
    print(f"\n[{label}]")
    print(f"  text    : {text}")
    print(f"  voice   : {voice}")
    print(f"  model   : {model or TTSConfig.from_env().model}")
    print(f"  speed   : {speed}")
    print(f"  segments: {len(segments)}")
    if len(segments) > 1:
        for i, seg in enumerate(segments, 1):
            print(f"    part {i}: {seg}")

    t0 = time.time()
    try:
        if use_postprocess:
            result_path = synthesize_speech(
                text=text,
                voice=voice,
                output_path=output_path,
                model=model,
            )
        else:
            service = TTSService()
            result_path = service.synthesize(
                text=text,
                voice=voice,
                output_path=output_path,
                response_format="mp3",
                speed=speed,
                model=model,
            )
    except (TTSConfigurationError, TTSAPIError, ValueError) as exc:
        elapsed = time.time() - t0
        print(f"  status  : FAILED ({elapsed:.1f}s)")
        print(f"  error   : {exc}")
        return {"ok": False, "label": label, "error": str(exc), "elapsed": elapsed}

    elapsed = time.time() - t0
    file_path = Path(result_path)
    size_kb = file_path.stat().st_size / 1024 if file_path.exists() else 0
    duration = _audio_duration(result_path)

    print(f"  status  : OK ({elapsed:.1f}s)")
    print(f"  output  : {result_path}")
    print(f"  size    : {size_kb:.1f} KB")
    if duration is not None:
        print(f"  duration: {duration:.2f}s")
    else:
        print("  duration: (moviepy 不可用，跳过时长检测)")

    for warning in _inspect_saved_file(result_path):
        print(f"  warning : {warning}")

    return {
        "ok": True,
        "label": label,
        "path": result_path,
        "elapsed": elapsed,
        "size_kb": size_kb,
        "duration": duration,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试一展 TTS 语音生成接口")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要合成的文本")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="音色名，如 Cherry")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="TTS 模型名")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 mp3 路径")
    parser.add_argument("--speed", type=float, default=1.0, help="语速，默认 1.0")
    parser.add_argument("--long", action="store_true", help="额外跑一条长文本分段测试")
    parser.add_argument("--raw", action="store_true", help="只测试原始 HTTP 响应，便于排查接口格式")
    parser.add_argument(
        "--postprocess",
        action="store_true",
        help="通过 generation.postprocess.synthesize_speech 调用（验证主流程封装）",
    )
    parser.add_argument("--list-voices", action="store_true", help="打印常用音色名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_voices:
        print("常用音色名（具体可用性请在一展控制台确认）：")
        for name in COMMON_VOICES:
            print(f"  - {name}")
        return 0

    cfg = print_config()
    if not cfg.api_key:
        print("\n错误：未配置 YIZHAN_API_KEY，请在 .env 中设置后再试。")
        return 1

    voice = (args.voice or config.YIZHAN_TTS_DEFAULT_VOICE or cfg.default_voice).strip()
    model = (args.model or config.YIZHAN_TTS_MODEL or cfg.model).strip() or None

    if args.raw:
        return run_raw_request(args.text, voice, model or cfg.model, args.speed)

    output = args.output.strip() or None

    results = []
    results.append(
        run_case(
            label="short",
            text=args.text,
            voice=voice,
            model=model,
            output=output,
            speed=args.speed,
            use_postprocess=args.postprocess,
        )
    )

    if args.long:
        results.append(
            run_case(
                label="long",
                text=LONG_TEXT,
                voice=voice,
                model=model,
                output=None,
                speed=args.speed,
                use_postprocess=args.postprocess,
            )
        )

    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)
    ok_count = sum(1 for r in results if r.get("ok"))
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        extra = f" -> {r.get('path')}" if r.get("path") else f" -> {r.get('error')}"
        print(f"  [{r['label']}] {status} ({r.get('elapsed', 0):.1f}s){extra}")

    print(f"\n通过 {ok_count}/{len(results)}")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
