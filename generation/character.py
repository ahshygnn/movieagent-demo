"""
人物定妆图生成（Seedream 5.0 / 火山方舟）。

针对剧本里的人物，先用 Character Designer agent 从剧本提取每人的英文外貌与
剧本环境背景，再调用与关键帧相同的 Seedream 5.0 API 生成全身正面定妆照，
落盘到 outputs/characters/，产出可直接赋给 task["character_refs"] 的 {名字: 路径} 字典。
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from generation.image import _call_api, _no_proxy
from agents.character_designer import run_character_designer_agent


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


def _safe_filename(name: str) -> str:
    """把角色名清成安全文件名（保留中文，剔除路径/非法字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip().strip(".")
    return cleaned or "character"


def _download(image_url: str, save_path: str) -> None:
    last_err = None
    for attempt in range(3):
        try:
            with _no_proxy() as session:
                r = session.get(image_url, timeout=120, stream=True)
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
    image_url = None
    for i in range(3):
        try:
            image_url = _call_api(
                prompt,
                [],
                size=config.CHARACTER_IMAGE_SIZE,
                timeout=180,
            )
            break
        except Exception as e:
            last_err = e
            print(f"  [{name}] Seedream 5.0 attempt {i + 1}/3 failed: {e}", flush=True)
            time.sleep(3)
    else:
        raise Exception(f"[{name}] Seedream 5.0 generation failed after 3 attempts: {last_err}")

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
