"""
火山方舟 Seedream 5.0 lite 图片生成
支持最多 14 张角色参考图（输入图 + 输出图 ≤ 15）
"""
import time
import base64
import contextlib
import os
import shutil
import requests
import config


@contextlib.contextmanager
def _no_proxy():
    """Create an isolated direct session without mutating process-wide proxy state."""
    session = requests.Session()
    session.trust_env = False
    try:
        yield session
    finally:
        session.close()

# 加在 prompt 前，降低安全过滤误触发概率。风格由 config.VISUAL_STYLE 控制（可经 .env 切换）
def _safe_prefix() -> str:
    return f"Family-friendly animated film scene, {config.VISUAL_STYLE} style, safe for all ages. "


def _image_to_base64(path: str) -> str:
    """把本地图片转成 base64 data URL。"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def _get_keyframe_size(mode: str | None = None) -> str:
    """返回当前档位对应的关键帧尺寸字符串。"""
    m = mode or config.KEYFRAME_MODE
    return config.KEYFRAME_SIZE_DRAFT if m == "draft" else config.KEYFRAME_SIZE_FINAL


def _call_api(prompt: str, ref_images: list, size: str = "", timeout: int = 150) -> str:
    """调用 Seedream API，返回图片 URL。失败时抛出异常。"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.ARK_API_KEY}",
    }
    payload = {
        "model": "doubao-seedream-5-0-260128",
        "prompt": prompt,
        "size": size or _get_keyframe_size(),
        "output_format": "png",
        "watermark": False,
        "response_format": "url",
    }
    if ref_images:
        payload["image"] = ref_images if len(ref_images) > 1 else ref_images[0]

    with _no_proxy() as session:
        resp = session.post(
            "https://ark.cn-beijing.volces.com/api/v3/images/generations",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    if resp.status_code != 200:
        raise Exception(f"Seedream API 请求失败 {resp.status_code}: {resp.text}")
    return resp.json()["data"][0]["url"]


def _generate_and_download(prompt_core: str, ref_images: list, size: str, target_path: str) -> None:
    """生成一张图并下载到 target_path。保留原敏感/超时三次重试 + 下载三次重试。"""
    # 重试策略：
    #   第 1 次：加 safe prefix + 参考图
    #   第 2 次：加 safe prefix + 无参考图（排除参考图干扰）
    #   第 3 次：只用简化 prompt + 无参考图
    safe_prefix = _safe_prefix()
    attempts = [
        (safe_prefix + prompt_core, ref_images),
        (safe_prefix + prompt_core, []),
        (safe_prefix + "Animated movie scene. " + prompt_core[:200], []),
    ]

    last_err = None
    image_url = None
    for i, (prompt, images) in enumerate(attempts):
        try:
            image_url = _call_api(prompt, images, size=size)
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "SensitiveContent" in err_str or "sensitive" in err_str.lower():
                print(f"  [内容过滤，尝试 {i+2}/3] 简化 prompt 重试...")
                time.sleep(2)
                continue
            elif "timed out" in err_str.lower() or "timeout" in err_str.lower():
                print(f"  [超时，尝试 {i+2}/3] 重试...")
                time.sleep(5)
                continue
            else:
                raise  # 其他错误直接抛出
    else:
        raise Exception(f"生成关键帧失败（已重试 3 次）：{last_err}")

    # 下载图片保存到本地（带重试，大图下载易断连）
    os.makedirs(config.KEYFRAME_DIR, exist_ok=True)
    last_dl_err = None
    for dl_attempt in range(3):
        try:
            with _no_proxy() as session:
                img_resp = session.get(image_url, timeout=120, stream=True)
                img_resp.raise_for_status()
                with open(target_path, "wb") as f:
                    for chunk in img_resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
            return
        except Exception as e:
            last_dl_err = e
            print(f"  [图片下载重试 {dl_attempt+2}/3] {e}")
            time.sleep(3)
    raise Exception(f"图片下载失败（已重试 3 次）：{last_dl_err}")


def generate_keyframe(shot_plot: str, shot_id: str,
                      character_refs: dict = None,
                      mode: str | None = None,
                      appearance_texts: dict | None = None,
                      visual_style: str | None = None) -> dict:
    """
    调用火山方舟 Seedream 5.0 生成关键帧。

    character_refs: {"角色名": "/local/path/to/image.png", ...}，最多 14 张。
    mode: "draft" / "final"，None 则读 config.KEYFRAME_MODE。
    appearance_texts: {"角色名": "英文外貌描述"}，供反思审核人物一致性作辅助依据。
    visual_style: 目标画风，None 则取 config.VISUAL_STYLE。

    启用 config.ENABLE_KEYFRAME_REFLECTION 时：每次生成后用审核模型审 4 维，
    出现 severe 则带英文修正反馈重生成（最多 1+KEYFRAME_REFLECTION_MAX_RETRIES 次），
    择优保底（severe 更少、总分更高者胜出），审核报告放进返回 dict 的 "review"。
    关闭时行为与改动前完全一致。
    """
    start = time.time()
    size = _get_keyframe_size(mode)
    visual_style = visual_style or config.VISUAL_STYLE

    # 构建参考图列表
    ref_images = []
    if character_refs:
        for _name, path in list(character_refs.items())[:14]:
            if os.path.isfile(path):
                ref_images.append(_image_to_base64(path))

    os.makedirs(config.KEYFRAME_DIR, exist_ok=True)
    final_path = os.path.join(config.KEYFRAME_DIR, f"{shot_id}.png")

    # —— 反思关闭：与改动前一致，直接生成落盘 ——
    if not config.ENABLE_KEYFRAME_REFLECTION:
        _generate_and_download(shot_plot, ref_images, size, final_path)
        return {
            "local_path": final_path,
            "elapsed_seconds": round(time.time() - start, 2),
            "inference_time": 0,
            "keyframe_size": size,
            "review": None,
        }

    # —— 反思-重生成循环：severe 才重生成，择优保底 ——
    from generation.reflection import review_keyframe

    n_attempts = 1 + max(0, int(config.KEYFRAME_REFLECTION_MAX_RETRIES))
    best = None  # (rank_tuple, cand_path, report)
    feedback = ""
    cand_paths = []
    for i in range(n_attempts):
        cand_path = os.path.join(config.KEYFRAME_DIR, f"{shot_id}__cand{i}.png")
        cand_paths.append(cand_path)
        _generate_and_download(shot_plot + feedback, ref_images, size, cand_path)
        report = review_keyframe(
            cand_path,
            visual_style=visual_style,
            plot_description=shot_plot,
            character_refs=character_refs or {},
            appearance_texts=appearance_texts or {},
        )
        # 择优排序：severe 越少越好，其次总分越高越好
        rank = (-report["severe_count"], report["total_score"])
        if best is None or rank > best[0]:
            best = (rank, cand_path, report)
        print(f"  [反思 {i+1}/{n_attempts}] {shot_id} → {report['review_status']} "
              f"(severe={report['severe_count']}, score={report['total_score']})")
        if not report["needs_regeneration"]:
            break
        feedback = "\n\n[Fix the following issues]: " + (report["fix_instructions"] or "")

    # 把最佳候选复制为最终图，清理所有候选
    best_report = best[2]
    shutil.copyfile(best[1], final_path)
    for cand_path in cand_paths:
        if os.path.isfile(cand_path):
            try:
                os.remove(cand_path)
            except OSError:
                pass

    return {
        "local_path": final_path,
        "elapsed_seconds": round(time.time() - start, 2),
        "inference_time": 0,
        "keyframe_size": size,
        "review": best_report,
    }
