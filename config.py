import os
from pathlib import Path
from dotenv import load_dotenv

# 强制从当前文件所在目录加载 .env，不依赖工作目录
load_dotenv(Path(__file__).parent / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _openai_base_url(value: str) -> str:
    base = (value or "https://vip.yi-zhan.top/v1/").strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/"


GENERATION_MODE = os.getenv("GENERATION_MODE", "final").strip().lower() or "final"
IS_DRAFT_MODE = GENERATION_MODE == "draft"

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE", "").strip()
ENABLE_DUBBING = _env_bool("ENABLE_DUBBING", True)

# 火山引擎签名鉴权（即梦图片生成 4.0）
VOLC_ACCESS_KEY = os.getenv("VOLC_ACCESS_KEY", "")
VOLC_SECRET_KEY = os.getenv("VOLC_SECRET_KEY", "")

# 一展（LLM + OpenAI-compatible TTS）
YIZHAN_API_KEY = os.getenv("YIZHAN_API_KEY", "").strip()
YIZHAN_BASE_URL = _openai_base_url(os.getenv("YIZHAN_BASE_URL", "https://vip.yi-zhan.top/v1/"))
YIZHAN_TTS_MODEL = os.getenv("YIZHAN_TTS_MODEL", "qwen3-tts-flash").strip()
YIZHAN_TTS_DEFAULT_VOICE = (
    os.getenv("YIZHAN_TTS_DEFAULT_VOICE")
    or DEFAULT_TTS_VOICE
    or "Cherry"
).strip()
YIZHAN_TTS_TIMEOUT_SECONDS = float(os.getenv("YIZHAN_TTS_TIMEOUT_SECONDS", "120"))
YIZHAN_TTS_MAX_RETRIES = int(os.getenv("YIZHAN_TTS_MAX_RETRIES", "3"))

# 火山方舟（图片生成 Seedream + 视频生成 Seedance）
ARK_API_KEY = os.getenv("ARK_API_KEY", "")

# 模型名称
LLM_MODEL = "claude-sonnet-4-6"
IMAGE_MODEL = "jimeng_t2i_v40"
VIDEO_MODEL = "doubao-seedance-1-0-pro-fast-251015"

IMAGE_SIZE = "1024x1024"
VIDEO_SIZE = "960x960"

# 关键帧分辨率档位（Seedream doubao-seedream-5-0-260128 实测最小像素限制：3,686,400 px）
# 最小合法 16:9：2560×1440 = 3,686,400 px（API 下限，比原 2848×1600 少 ~19% 像素）
# draft: 2560x1440（最小合法 16:9，快速预览）
# final: 2848x1600（原始最高质量，保持不变）
# 注：1920×1080、1280×720 等常见档位均低于 API 最小像素要求，不可用
KEYFRAME_SIZE_DRAFT = os.getenv("KEYFRAME_SIZE_DRAFT", "2560x1440").strip()
KEYFRAME_SIZE_FINAL = os.getenv("KEYFRAME_SIZE_FINAL", "2848x1600").strip()
KEYFRAME_MODE = os.getenv("KEYFRAME_MODE", "draft" if IS_DRAFT_MODE else "final").strip()
KEYFRAME_MAX_CONCURRENCY = int(os.getenv("KEYFRAME_MAX_CONCURRENCY", "3"))
# Keep shot duration natural in draft mode too. Speed should come from
# concurrency/caching/resume, not from making every shot too short.
VIDEO_DURATION_SECONDS = int(os.getenv("VIDEO_DURATION_SECONDS", "5"))
VIDEO_RESOLUTION = os.getenv("VIDEO_RESOLUTION", "480p" if IS_DRAFT_MODE else "720p").strip()
SHOT_MAX_PER_SCENE = int(os.getenv("SHOT_MAX_PER_SCENE", "2" if IS_DRAFT_MODE else "0"))
VIDEO_MAX_CONCURRENCY = int(os.getenv("VIDEO_MAX_CONCURRENCY", "2"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

COST_TRACKING = True

VIDEO_POLL_INTERVAL = 5
VIDEO_POLL_MAX_RETRIES = 60

KEYFRAME_DIR = "outputs/keyframes"
VIDEO_DIR = "outputs/videos"
