"""
Optional CLIP-based metrics (L1.3 角色一致性, L2.2 视觉-文本对齐, L2.3 风格一致性).

Requires:  pip install torch torchvision transformers pillow
If not installed, all functions return {"available": False} without raising.
"""
from __future__ import annotations
import os

try:
    import torch
    from PIL import Image
    from transformers import CLIPProcessor, CLIPModel
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

_MODEL_NAME = "openai/clip-vit-base-patch32"
_model: "CLIPModel | None" = None
_processor: "CLIPProcessor | None" = None


def is_available() -> bool:
    return _AVAILABLE


def _load():
    global _model, _processor
    if _model is None:
        _model = CLIPModel.from_pretrained(_MODEL_NAME)
        _processor = CLIPProcessor.from_pretrained(_MODEL_NAME)
        _model.eval()


def _image_embedding(image_path: str) -> "torch.Tensor":
    _load()
    img = Image.open(image_path).convert("RGB")
    inputs = _processor(images=img, return_tensors="pt")
    with torch.no_grad():
        return _model.get_image_features(**inputs)


def _text_embedding(text: str) -> "torch.Tensor":
    _load()
    inputs = _processor(text=[text], return_tensors="pt", padding=True, truncation=True)
    with torch.no_grad():
        return _model.get_text_features(**inputs)


def _cosine(a: "torch.Tensor", b: "torch.Tensor") -> float:
    import torch.nn.functional as F
    return float(F.cosine_similarity(a, b).item())


# ── L2.2 视觉-文本对齐 (CLIP-T) ──────────────────────────────────────────────

def score_visual_text_alignment(task_id: str, shots: dict) -> dict:
    """
    shots: tasks[task_id]["shots"]
    Returns {shot_key: clip_t_score} and persists via collector.
    """
    if not _AVAILABLE:
        return {"available": False}

    from metrics.collector import record_clip_t_scores
    scores: dict[str, float] = {}
    for ss_name, scenes in shots.items():
        for scene_name, scene_data in scenes.items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                kf = shot_data.get("keyframe_local_path")
                plot = shot_data.get("Plot/Visual Description", "")
                if not kf or not os.path.isfile(kf) or not plot:
                    continue
                try:
                    img_emb = _image_embedding(kf)
                    txt_emb = _text_embedding(plot)
                    key = f"{ss_name}__{scene_name}__{shot_name}"
                    scores[key] = round(_cosine(img_emb, txt_emb), 4)
                except Exception as e:
                    print(f"[CLIP-T] {shot_name} 评分失败: {e}")

    record_clip_t_scores(task_id, scores)
    return {"available": True, "scores": scores}


# ── L2.3 风格一致性 (CLIP-I 跨镜头方差) ─────────────────────────────────────

def score_style_consistency(task_id: str, shots: dict) -> dict:
    """Compute variance of image embeddings across all shots as style drift proxy."""
    if not _AVAILABLE:
        return {"available": False}

    import torch

    embeddings = []
    for ss_name, scenes in shots.items():
        for scene_name, scene_data in scenes.items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                kf = shot_data.get("keyframe_local_path")
                if not kf or not os.path.isfile(kf):
                    continue
                try:
                    embeddings.append(_image_embedding(kf))
                except Exception as e:
                    print(f"[CLIP-I] {shot_name} embedding 失败: {e}")

    if len(embeddings) < 2:
        return {"available": True, "clip_i_variance": None, "mean_similarity": None}

    import torch.nn.functional as F
    stack = torch.cat(embeddings, dim=0)  # (N, D)
    normed = F.normalize(stack, dim=1)
    sim_matrix = normed @ normed.T  # (N, N)

    # Mean pairwise similarity (upper triangle, excluding diagonal)
    N = len(embeddings)
    pairs = [(i, j) for i in range(N) for j in range(i + 1, N)]
    sims = [float(sim_matrix[i, j].item()) for i, j in pairs]
    mean_sim = sum(sims) / len(sims)
    variance = sum((s - mean_sim) ** 2 for s in sims) / len(sims)

    from metrics.collector import record_style_consistency
    record_style_consistency(task_id, variance, mean_sim)
    return {"available": True, "clip_i_variance": variance, "mean_similarity": mean_sim}


# ── L1.3 角色一致性 (FaceNet via CLIP-I proxy) ───────────────────────────────

def score_character_consistency(task_id: str, shots: dict) -> dict:
    """
    Compute per-character embedding similarity across all shots they appear in.
    Uses CLIP image embeddings as a lightweight proxy for FaceNet face similarity.
    Target > 0.85 per the metrics spec.
    """
    if not _AVAILABLE:
        return {"available": False}

    import torch.nn.functional as F
    from metrics.collector import record_character_consistency

    # Collect keyframe embeddings per character
    char_embeddings: dict[str, list] = {}
    for ss_name, scenes in shots.items():
        for scene_name, scene_data in scenes.items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                kf = shot_data.get("keyframe_local_path")
                if not kf or not os.path.isfile(kf):
                    continue
                involving = shot_data.get("Involving Characters") or {}
                if not isinstance(involving, dict):
                    continue
                try:
                    emb = _image_embedding(kf)
                except Exception:
                    continue
                for char in involving:
                    char_embeddings.setdefault(char, []).append(emb)

    scores_per_char: dict[str, list[float]] = {}
    all_scores: list[float] = []
    for char, embs in char_embeddings.items():
        if len(embs) < 2:
            continue
        import torch
        stack = torch.cat(embs, dim=0)
        normed = F.normalize(stack, dim=1)
        sim_matrix = normed @ normed.T
        N = len(embs)
        pairs = [(i, j) for i in range(N) for j in range(i + 1, N)]
        sims = [round(float(sim_matrix[i, j].item()), 4) for i, j in pairs]
        scores_per_char[char] = sims
        all_scores.extend(sims)

    mean = round(sum(all_scores) / len(all_scores), 4) if all_scores else None
    record_character_consistency(task_id, scores_per_char, mean or 0.0)
    return {"available": True, "scores": scores_per_char, "mean": mean}
