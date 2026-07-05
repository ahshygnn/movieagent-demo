"""
人物定妆图生成（image-2 / gpt-image-2-all，一展平台）。

针对剧本里的人物，先用 Character Designer agent 从剧本提取每人的英文外貌与
剧本环境背景，再调用一展 /v1/chat/completions 上的 image-2 生成全身正面定妆照，
落盘到 outputs/characters/，产出可直接赋给 task["character_refs"] 的 {名字: 路径} 字典。

注意：这里走的是一展 chat/completions（返回体里带图片 URL 或 base64），
与 generation/image.py 的 Ark/Seedream 接口不同，不能复用其 _call_api。
"""
import base64
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config
from generation.image import _no_proxy
from agents.character_designer import run_character_designer_agent


# 从助手回复里提取图片 URL：优先 markdown 图片，其次裸图片链接
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_IMAGE_RE = re.compile(r"https?://\S+?\.(?:png|jpg|jpeg|webp)", re.IGNORECASE)
# base64 data URL 兜底（部分聚合模型直接把图内联在正文里）
_DATA_URL_RE = re.compile(r"data:image/(png|jpe?g|webp);base64,([A-Za-z0-9+/=]+)")


def _safe_prefix() -> str:
    """与 generation/image.py 一致的安全前缀，降低内容过滤误触发概率。"""
    return f"Family-friendly animated character reference, {config.VISUAL_STYLE} style, safe for all ages."


def build_character_prompt(name: str, appearance: str, background: str, feedback: str = "") -> str:
    """
    构造人物定妆图提示词（构图/风格约束已与用户审核确认）。
    feedback: 用户对上一版定妆图的修改意见，非空时作为高优先级修正要求追加到末尾。
    """
    appearance = (appearance or "").strip() or f"the character {name}"
    background = (background or "").strip() or "a simple scene that fits the story's setting"
    revision = ""
    if (feedback or "").strip():
        revision = (
            "\n\n[Revision requested by reviewer — must address]: "
            f"{feedback.strip()}"
        )
    return f"""{_safe_prefix()}

Full-body character reference of {name}: {appearance}.

[Composition rules]
- A SINGLE character only, centered in frame.
- ENTIRE body visible head-to-toe, feet included, no cropping, full-length shot.
- The character FACES THE CAMERA directly (frontal view), standing naturally in a relaxed upright A-pose (arms slightly away from the torso).
- Face, hairstyle, outfit and colors must be clear and readable.

[Background]
- {background}
- The background is a scene that fits the story's setting.

[Constraints]
- Keep the whole image strictly in the {config.VISUAL_STYLE} art style.
- No text, no watermark, no logos, no extra characters, no split panels.{revision}"""


def _extract_image(content: str) -> tuple[str | None, bytes | None]:
    """
    从 chat/completions 的 message.content 里解析图片。
    返回 (url, raw_bytes)：URL 分支返回 (url, None)，内联 base64 分支返回 (None, bytes)。
    都解析不到则返回 (None, None)。
    """
    if not content:
        return None, None
    m = _MD_IMAGE_RE.search(content)
    if m:
        return m.group(1), None
    m = _DATA_URL_RE.search(content)
    if m:
        try:
            return None, base64.b64decode(m.group(2))
        except Exception:
            pass
    m = _BARE_IMAGE_RE.search(content)
    if m:
        return m.group(0), None
    return None, None


def _call_image2_api(prompt: str, timeout: int = 180) -> tuple[str | None, bytes | None]:
    """
    调用一展 image-2（chat/completions），返回 (图片URL, 内联bytes) 二选一。
    失败或解析不到图片时抛异常（异常里带上原始返回片段，便于按真实格式定型）。
    """
    url = f"{config.YIZHAN_BASE_URL}chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.YIZHAN_API_KEY}",
    }
    payload = {
        "model": config.IMAGE2_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise Exception(f"image-2 API 请求失败 {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise Exception(f"image-2 返回体解析失败: {e}; 原始返回: {resp.text[:500]}")

    # content 可能是字符串，也可能是分段的 list（部分模型）
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )

    image_url, raw = _extract_image(content)
    if not image_url and raw is None:
        raise Exception(f"image-2 未能从返回中解析出图片；content 头部: {str(content)[:500]}")
    return image_url, raw


def _safe_filename(name: str) -> str:
    """把角色名清成安全文件名（保留中文，剔除路径/非法字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip().strip(".")
    return cleaned or "character"


def _download(image_url: str, save_path: str) -> None:
    last_err = None
    for attempt in range(3):
        try:
            with _no_proxy():
                r = requests.get(image_url, timeout=120, stream=True)
            r.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
            return
        except Exception as e:
            last_err = e
            print(f"  [人物图下载重试 {attempt + 1}/3] {e}", flush=True)
            time.sleep(3)
    raise Exception(f"人物图下载失败（已重试 3 次）：{last_err}")


def generate_character_reference(
    name: str,
    appearance: str,
    background: str = "",
    save_dir: str | None = None,
    filename: str | None = None,
    feedback: str = "",
) -> dict:
    """
    生成单个角色的全身正面定妆照，落盘到 outputs/characters/<name>.png。
    feedback: 用户对上一版的修改意见（人工审核后带反馈重生成时传入）。
    返回 {"name", "path", "elapsed_seconds"}。
    """
    start = time.time()
    save_dir = save_dir or config.CHARACTER_DIR
    os.makedirs(save_dir, exist_ok=True)
    fname = filename or f"{_safe_filename(name)}.png"
    save_path = os.path.join(save_dir, fname)

    prompt = build_character_prompt(name, appearance, background, feedback=feedback)

    last_err = None
    image_url = raw = None
    for i in range(3):
        try:
            with _no_proxy():
                image_url, raw = _call_image2_api(prompt)
            break
        except Exception as e:
            last_err = e
            print(f"  [{name}] image-2 尝试 {i + 1}/3 失败: {e}", flush=True)
            time.sleep(3)
    else:
        raise Exception(f"[{name}] image-2 生成失败（已重试 3 次）：{last_err}")

    if raw is not None:
        with open(save_path, "wb") as f:
            f.write(raw)
    else:
        _download(image_url, save_path)

    elapsed = round(time.time() - start, 2)
    print(f"  [{name}] 完成，耗时 {elapsed}s → {save_path}", flush=True)
    return {"name": name, "path": save_path, "elapsed_seconds": elapsed}


def generate_character_references(
    movie_script: str,
    characters: list[str],
    concurrency: int | None = None,
) -> dict:
    """
    编排器：先用 Designer agent 从剧本提取每人 Appearance/Background，
    再并发生成定妆图。返回 {"character_refs": {名字: 路径}, "designs": {...}, "errors": {...}, "usage": {...}}。
    单个角色失败不影响其它角色。
    """
    characters = [c for c in (characters or []) if str(c).strip()]
    if not characters:
        return {"character_refs": {}, "designs": {}, "errors": {}, "usage": {}}

    designer_out = run_character_designer_agent(movie_script, characters)
    designs = designer_out["result"].get("Characters", {})

    workers = min(concurrency or config.KEYFRAME_MAX_CONCURRENCY, len(characters))
    character_refs: dict[str, str] = {}
    errors: dict[str, str] = {}

    def _one(name: str) -> tuple[str, dict | None, str | None]:
        entry = designs.get(name, {}) or {}
        try:
            res = generate_character_reference(
                name,
                entry.get("Appearance", ""),
                entry.get("Background", ""),
            )
            return name, res, None
        except Exception as e:
            return name, None, str(e)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_one, name): name for name in characters}
        for future in as_completed(futures):
            name, res, err = future.result()
            if res:
                character_refs[name] = res["path"]
            else:
                errors[name] = err or "unknown error"
                print(f"  [{name}] ERROR: {err}", flush=True)

    return {
        "character_refs": character_refs,
        "designs": designs,
        "errors": errors,
        "usage": designer_out.get("usage", {}),
    }
