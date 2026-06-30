"""
Central metrics collector.

Each task gets its own JSON file at outputs/metrics/{task_id}_metrics.json.
All helpers are stateless functions; they load→mutate→save on every call.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

METRICS_DIR = Path("outputs/metrics")


# ── file helpers ──────────────────────────────────────────────────────────────

def _path(task_id: str) -> Path:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    return METRICS_DIR / f"{task_id}_metrics.json"


def load_metrics(task_id: str) -> dict:
    p = _path(task_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _default(task_id)


def save_metrics(task_id: str, metrics: dict):
    _path(task_id).write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── schema ────────────────────────────────────────────────────────────────────

def _default(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "created_at": _now(),
        "l1": {
            "pipeline_completion": {
                "steps": {},           # step_name → {status, elapsed_seconds}
                "json_parse_attempts": 0,
                "json_parse_successes": 0,
                "json_parse_rate": None,
                "completion_rate": None,
            },
            "latency": {
                "director_seconds": None,
                "scene_total_seconds": None,
                "shot_total_seconds": None,
                "pipeline_planning_seconds": None,
                "keyframe_seconds": {},   # shot_id → seconds
                "video_seconds": {},      # shot_id → seconds
            },
            "character_consistency": {
                "scores": {},     # character_name → [sim_score, ...]
                "mean": None,
                "available": False,
                "note": "Requires: pip install torch torchvision transformers pillow",
            },
        },
        "l2": {
            "narrative_coherence": {
                "scores": {},   # key → {score, reason}
                "mean": None,
                "status": "pending",   # pending | running | done | error
            },
            "visual_text_alignment": {
                "scores": {},   # shot_id → clip_t_score
                "mean": None,
                "available": False,
                "note": "Requires: pip install torch torchvision transformers pillow",
            },
            "style_consistency": {
                "clip_i_variance": None,
                "mean_similarity": None,
                "available": False,
                "note": "Requires: pip install torch torchvision transformers pillow",
            },
        },
        "l3": {
            "task_start_time": None,
            "task_end_time": None,
            "duration_minutes": None,
            "first_video_satisfaction": None,   # "yes" | "no"
            "controllability_rating": None,      # 1-5
        },
    }


def init_metrics(task_id: str) -> dict:
    m = _default(task_id)
    m["l3"]["task_start_time"] = _now()
    save_metrics(task_id, m)
    return m


# ── L1.1 pipeline completion ──────────────────────────────────────────────────

def record_step(task_id: str, step_name: str, status: str, elapsed: float):
    m = load_metrics(task_id)
    m["l1"]["pipeline_completion"]["steps"][step_name] = {
        "status": status,
        "elapsed_seconds": round(elapsed, 2),
    }
    _refresh_completion_rate(m)
    save_metrics(task_id, m)


def record_parse_stats(task_id: str, attempts: int, successes: int):
    m = load_metrics(task_id)
    comp = m["l1"]["pipeline_completion"]
    comp["json_parse_attempts"] += attempts
    comp["json_parse_successes"] += successes
    total = comp["json_parse_attempts"]
    comp["json_parse_rate"] = round(comp["json_parse_successes"] / total, 3) if total else None
    save_metrics(task_id, m)


def _refresh_completion_rate(m: dict):
    steps = m["l1"]["pipeline_completion"]["steps"]
    total = len(steps)
    if total == 0:
        return
    success = sum(1 for s in steps.values() if s["status"] == "success")
    m["l1"]["pipeline_completion"]["completion_rate"] = round(success / total, 3)


# ── L1.2 latency ──────────────────────────────────────────────────────────────

def record_phase_latency(task_id: str, phase: str, seconds: float):
    m = load_metrics(task_id)
    m["l1"]["latency"][phase] = round(seconds, 2)
    save_metrics(task_id, m)


def record_keyframe_latency(task_id: str, shot_id: str, seconds: float):
    m = load_metrics(task_id)
    m["l1"]["latency"]["keyframe_seconds"][shot_id] = round(seconds, 2)
    save_metrics(task_id, m)


def record_video_latency(task_id: str, shot_id: str, seconds: float):
    m = load_metrics(task_id)
    m["l1"]["latency"]["video_seconds"][shot_id] = round(seconds, 2)
    save_metrics(task_id, m)


# ── L2.1 narrative coherence (written by l2_content.py) ──────────────────────

def record_narrative_scores(task_id: str, scores: dict):
    m = load_metrics(task_id)
    valid = [v["score"] for v in scores.values() if isinstance(v.get("score"), (int, float))]
    m["l2"]["narrative_coherence"]["scores"] = scores
    m["l2"]["narrative_coherence"]["mean"] = round(sum(valid) / len(valid), 2) if valid else None
    m["l2"]["narrative_coherence"]["status"] = "done"
    save_metrics(task_id, m)


# ── L2.2 / L2.3 CLIP (written by clip_scorer.py) ─────────────────────────────

def record_clip_t_scores(task_id: str, scores: dict):
    m = load_metrics(task_id)
    valid = [v for v in scores.values() if isinstance(v, (int, float))]
    m["l2"]["visual_text_alignment"]["scores"] = scores
    m["l2"]["visual_text_alignment"]["mean"] = round(sum(valid) / len(valid), 4) if valid else None
    m["l2"]["visual_text_alignment"]["available"] = True
    save_metrics(task_id, m)


def record_style_consistency(task_id: str, variance: float, mean_sim: float):
    m = load_metrics(task_id)
    m["l2"]["style_consistency"]["clip_i_variance"] = round(variance, 6)
    m["l2"]["style_consistency"]["mean_similarity"] = round(mean_sim, 4)
    m["l2"]["style_consistency"]["available"] = True
    save_metrics(task_id, m)


def record_character_consistency(task_id: str, scores_per_char: dict, mean: float):
    m = load_metrics(task_id)
    m["l1"]["character_consistency"]["scores"] = scores_per_char
    m["l1"]["character_consistency"]["mean"] = round(mean, 4)
    m["l1"]["character_consistency"]["available"] = True
    save_metrics(task_id, m)


# ── L3 user feedback ──────────────────────────────────────────────────────────

def record_l3_feedback(task_id: str, satisfaction: str | None, rating: int | None):
    m = load_metrics(task_id)
    now = _now()
    m["l3"]["task_end_time"] = now
    if satisfaction is not None:
        m["l3"]["first_video_satisfaction"] = satisfaction
    if rating is not None:
        m["l3"]["controllability_rating"] = rating
    start = m["l3"].get("task_start_time")
    if start:
        try:
            from datetime import datetime, timezone
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(start)
            m["l3"]["duration_minutes"] = round(delta.total_seconds() / 60, 1)
        except Exception:
            pass
    save_metrics(task_id, m)
