"""
L2 内容质量指标（VLM-as-judge）
基于 AnimationBench (arXiv:2604.15299) 方法论：
  - 每个维度拆解为 Yes/No 问题组
  - VLM 逐题作答，聚合 Yes 比例转换为 1-5 分

- 2.1 文本-视觉对齐（Shot 级别，Anthropic multimodal）
- 2.2 视觉风格一致性（全片级别，CLIP-I）
- 2.3 叙事连贯性（全片级别，Anthropic multimodal）
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# 允许从 evaluation/ 目录或项目根目录运行
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


# ── Anthropic client ──────────────────────────────────────────────────────────

def _make_client():
    try:
        import anthropic
    except ImportError:
        raise ImportError("请安装 anthropic SDK：pip install anthropic")
    api_key = getattr(config, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY 未配置。\n"
            "请在项目根目录的 .env 文件中添加：ANTHROPIC_API_KEY=your_key_here"
        )
    import anthropic as _anthropic
    return _anthropic.Anthropic(api_key=api_key)


# ── 图像工具 ──────────────────────────────────────────────────────────────────

def _to_base64(path: str) -> tuple[str, str]:
    """返回 (base64_str, media_type)"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
    media_type = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }.get(ext, "image/png")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _image_block(path: str) -> dict:
    data, mt = _to_base64(path)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mt, "data": data},
    }


# ── JSON 解析工具 ─────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # fence 清洗后再试
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _yes_count(qa: dict, keys: list[str]) -> int:
    return sum(1 for k in keys if qa.get(k) is True)


def _to_5(yes: int, total: int) -> float:
    """Yes 比例线性映射到 1-5 分"""
    ratio = yes / total if total > 0 else 0.0
    return round(1.0 + ratio * 4.0, 2)


# ── 2.1 文本-视觉对齐 ─────────────────────────────────────────────────────────

_ALIGNMENT_PROMPT = """\
你是一位动画评审专家。请观察提供的动画视频帧，对照以下剧本描述进行评估。

剧本描述：{plot}

请回答以下问题（仅回答 Yes 或 No，然后用一句话说明理由）：

Q1: 画面中的场景/地点是否与描述一致？
Q2: 描述中提到的角色是否出现在画面中？
Q3: 角色正在进行的动作是否符合描述？
Q4: 画面的整体情绪氛围是否与描述一致？

最后，请用 JSON 格式输出：
{{
  "scene_match": true/false,
  "character_present": true/false,
  "action_match": true/false,
  "mood_match": true/false,
  "reasoning": "简短说明主要问题"
}}"""

_ALIGNMENT_QA_KEYS = ["scene_match", "character_present", "action_match", "mood_match"]


def eval_text_visual_alignment(
    shot_data: dict,
    keyframe_path: str,
    anthropic_client,
    shot_name: str = "",
) -> dict:
    """
    对单个 Shot 进行文本-视觉对齐评分。

    返回：
    {
        "shot_name": str,
        "plot_description": str,
        "qa_results": {...},
        "alignment_score": float,   # 1-5
        "vlm_reasoning": str,
        "error": str | None
    }
    """
    plot = (shot_data or {}).get("Plot/Visual Description", "")

    try:
        prompt = _ALIGNMENT_PROMPT.format(plot=plot)
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    _image_block(keyframe_path),
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        qa = _parse_json(response.content[0].text)
        yes = _yes_count(qa, _ALIGNMENT_QA_KEYS)
        return {
            "shot_name": shot_name,
            "plot_description": plot,
            "qa_results": {k: bool(qa.get(k)) for k in _ALIGNMENT_QA_KEYS},
            "alignment_score": _to_5(yes, len(_ALIGNMENT_QA_KEYS)),
            "vlm_reasoning": qa.get("reasoning", ""),
            "error": None,
        }
    except Exception as e:
        return {
            "shot_name": shot_name,
            "plot_description": plot,
            "qa_results": {},
            "alignment_score": None,
            "vlm_reasoning": None,
            "error": str(e),
        }


async def _eval_alignment_async(
    shots_to_eval: list[dict],
    anthropic_client,
    max_concurrency: int = 3,
) -> list[dict]:
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(info: dict) -> dict:
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: eval_text_visual_alignment(
                    info["shot_data"],
                    info["keyframe_path"],
                    anthropic_client,
                    info["shot_name"],
                ),
            )

    return await asyncio.gather(*[_one(s) for s in shots_to_eval])


def eval_all_text_visual_alignment(task_id: str, tasks_dict: dict, anthropic_client) -> dict:
    """
    批量评测任务中所有已有关键帧的 Shot，并发数限 3。

    返回：
    {
        "shots_evaluated": int,
        "shots_skipped": int,       # 无关键帧的 shot
        "average_score": float,
        "per_shot": [...]
    }
    """
    task = tasks_dict.get(task_id) or {}
    to_eval: list[dict] = []
    skipped = 0

    for ss_name, scenes_dict in (task.get("shots") or {}).items():
        for scene_name, scene_data in (scenes_dict or {}).items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                kf = (shot_data or {}).get("keyframe_local_path")
                if kf and os.path.isfile(kf) and (shot_data or {}).get("keyframe_status") == "done":
                    to_eval.append({
                        "shot_name": f"{ss_name}/{scene_name}/{shot_name}",
                        "shot_data": shot_data,
                        "keyframe_path": kf,
                    })
                else:
                    skipped += 1

    if not to_eval:
        return {"shots_evaluated": 0, "shots_skipped": skipped, "average_score": None, "per_shot": []}

    results = asyncio.run(_eval_alignment_async(to_eval, anthropic_client))

    valid_scores = [r["alignment_score"] for r in results if r.get("alignment_score") is not None]
    avg = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None

    return {
        "shots_evaluated": len(results),
        "shots_skipped": skipped,
        "average_score": avg,
        "per_shot": results,
    }


# ── 2.2 视觉风格一致性（CLIP-I）─────────────────────────────────────────────

def eval_style_consistency(keyframe_paths: list[str]) -> dict:
    """
    用 CLIP-I embedding 方差衡量全片风格漂移。

    返回：
    {
        "frame_count": int,
        "embedding_variance": float,
        "pairwise_similarities": list,
        "min_similarity": float,
        "consistency_score": float,   # 1-5，方差越小分越高
        "available": bool
    }
    """
    try:
        import clip
        import torch
        from PIL import Image
    except ImportError:
        return {
            "frame_count": len(keyframe_paths),
            "available": False,
            "error": (
                "CLIP 未安装，请执行：\n"
                "pip install git+https://github.com/openai/CLIP.git Pillow numpy"
            ),
        }

    valid = [p for p in keyframe_paths if p and os.path.isfile(p)]
    if len(valid) < 2:
        return {
            "frame_count": len(valid),
            "available": True,
            "error": f"有效帧数不足（{len(valid)} < 2），无法计算一致性",
        }

    model, preprocess = clip.load("ViT-L/14", device="cpu")
    model.eval()

    embeddings = []
    for path in valid:
        img = preprocess(Image.open(path).convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            emb = model.encode_image(img)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            embeddings.append(emb)

    emb_matrix = torch.cat(embeddings, dim=0)
    variance = float(emb_matrix.var(dim=0).mean().item())

    pairwise = []
    for i in range(len(embeddings) - 1):
        sim = float(
            torch.nn.functional.cosine_similarity(embeddings[i], embeddings[i + 1]).item()
        )
        pairwise.append(round(sim, 4))

    min_sim = min(pairwise) if pairwise else None

    # variance → score: < 0.005 → 5, > 0.05 → 1（线性映射）
    score = max(1.0, min(5.0, 5.0 - (variance / 0.05) * 4.0))

    return {
        "frame_count": len(valid),
        "embedding_variance": round(variance, 6),
        "pairwise_similarities": pairwise,
        "min_similarity": min_sim,
        "consistency_score": round(score, 2),
        "available": True,
    }


def collect_keyframe_paths(task_id: str, tasks_dict: dict) -> list[str]:
    """按 Sub-Script → Scene → Shot 顺序收集所有已生成的关键帧路径。"""
    task = tasks_dict.get(task_id) or {}
    paths = []
    for scenes_dict in (task.get("shots") or {}).values():
        for scene_data in (scenes_dict or {}).values():
            for shot_data in (scene_data or {}).get("Shot", {}).values():
                kf = (shot_data or {}).get("keyframe_local_path")
                if kf and os.path.isfile(kf):
                    paths.append(kf)
    return paths


# ── 2.3 叙事连贯性 ───────────────────────────────────────────────────────────

_COHERENCE_PROMPT = """\
你是一位动画短片评审专家。以下是一部动画短片的原始剧本和按顺序排列的关键帧截图。

原始剧本：
{script}

请观察所有帧后回答（仅回答 Yes 或 No）：

Q1: 同一角色在不同镜头中外貌（脸型、服装、颜色）是否基本一致？
Q2: 视频中展示的故事情节是否前后连贯、有逻辑？
Q3: 视频内容是否忠实还原了原始剧本的主要情节？
Q4: 视频的整体情感基调是否与剧本一致？

请以 JSON 格式输出：
{{
  "character_consistent": true/false,
  "story_coherent": true/false,
  "script_faithful": true/false,
  "mood_consistent": true/false,
  "coherence_score": 1-5,
  "main_issues": "主要问题，如无问题则填'无'"
}}"""

_COHERENCE_QA_KEYS = [
    "character_consistent", "story_coherent", "script_faithful", "mood_consistent"
]


def eval_narrative_coherence(
    original_script: str,
    shot_sequence: list[dict],
    anthropic_client,
    max_frames: int = 8,
) -> dict:
    """
    shot_sequence: [{"shot_name": str, "keyframe_path": str, "plot": str}]

    返回：
    {
        "qa_results": {...},
        "coherence_score": float,   # 1-5
        "main_issues": str,
        "frames_evaluated": int,
        "error": str | None
    }
    """
    valid = [
        s for s in shot_sequence
        if s.get("keyframe_path") and os.path.isfile(s["keyframe_path"])
    ]

    # 均匀采样最多 max_frames 帧
    if len(valid) > max_frames:
        step = len(valid) / max_frames
        valid = [valid[int(i * step)] for i in range(max_frames)]

    if not valid:
        return {
            "qa_results": {},
            "coherence_score": None,
            "main_issues": None,
            "frames_evaluated": 0,
            "error": "no valid keyframes",
        }

    try:
        content: list[dict] = []
        for i, shot in enumerate(valid):
            content.append({"type": "text", "text": f"[帧 {i + 1}：{shot['shot_name']}]"})
            content.append(_image_block(shot["keyframe_path"]))

        content.append({
            "type": "text",
            "text": _COHERENCE_PROMPT.format(script=original_script),
        })

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": content}],
        )
        qa = _parse_json(response.content[0].text)
        yes = _yes_count(qa, _COHERENCE_QA_KEYS)

        # 优先用 VLM 自评分数，fallback 用 Yes 比例计算
        vlm_score = qa.get("coherence_score")
        if isinstance(vlm_score, (int, float)) and 1 <= vlm_score <= 5:
            score = round(float(vlm_score), 2)
        else:
            score = _to_5(yes, len(_COHERENCE_QA_KEYS))

        return {
            "qa_results": {k: bool(qa.get(k)) for k in _COHERENCE_QA_KEYS},
            "coherence_score": score,
            "main_issues": qa.get("main_issues", ""),
            "frames_evaluated": len(valid),
            "error": None,
        }
    except Exception as e:
        return {
            "qa_results": {},
            "coherence_score": None,
            "main_issues": None,
            "frames_evaluated": len(valid),
            "error": str(e),
        }


def build_shot_sequence(task_id: str, tasks_dict: dict) -> list[dict]:
    """构建评测用的 shot_sequence 列表（按 Sub-Script → Scene → Shot 顺序）。"""
    task = tasks_dict.get(task_id) or {}
    seq = []
    for ss_name, scenes_dict in (task.get("shots") or {}).items():
        for scene_name, scene_data in (scenes_dict or {}).items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                kf = (shot_data or {}).get("keyframe_local_path")
                seq.append({
                    "shot_name": f"{ss_name}/{scene_name}/{shot_name}",
                    "keyframe_path": kf or "",
                    "plot": (shot_data or {}).get("Plot/Visual Description", ""),
                })
    return seq
