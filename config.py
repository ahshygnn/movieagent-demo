import os
from pathlib import Path
from dotenv import load_dotenv

# 强制从当前文件所在目录加载 .env，不依赖工作目录
load_dotenv(Path(__file__).parent / ".env")

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE", "").strip()
SUBTITLE_FONT = os.getenv("SUBTITLE_FONT", "Microsoft YaHei").strip() or "Microsoft YaHei"
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "28"))

# 火山引擎签名鉴权（即梦图片生成 4.0）
VOLC_ACCESS_KEY = os.getenv("VOLC_ACCESS_KEY", "")
VOLC_SECRET_KEY = os.getenv("VOLC_SECRET_KEY", "")

# 一展（视频生成 + LLM）
YIZHAN_API_KEY = os.getenv("YIZHAN_API_KEY", "")
YIZHAN_BASE_URL = "https://vip.yi-zhan.top"

# 火山方舟（图片生成 Seedream + 视频生成 Seedance）
ARK_API_KEY = os.getenv("ARK_API_KEY", "")

# 模型名称
LLM_MODEL = "claude-sonnet-4-6"
IMAGE_MODEL = "jimeng_t2i_v40"
VIDEO_MODEL = "doubao-seedance-1-0-pro-fast-251015"

IMAGE_SIZE = "1024x1024"
VIDEO_SIZE = "960x960"

COST_TRACKING = True

VIDEO_POLL_INTERVAL = 5
VIDEO_POLL_MAX_RETRIES = 60

KEYFRAME_DIR = "outputs/keyframes"
VIDEO_DIR = "outputs/videos"
