"""Build sanitized, read-only case snapshots from local completed tasks."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "outputs" / "tasks.json"
SNAPSHOTS_PATH = ROOT / "data" / "case_snapshots.json"
ASSETS_DIR = ROOT / "case_assets"

CASES = [
    {
        "id": "forest-library",
        "task_id": "10015e62-b575-4dec-ae27-fcceffb70349",
        "title": "森林图书馆的借阅者",
        "meta": "22 镜头 · 720p · 已配音",
        "video": "/videos/10015e62-b575-4dec-ae27-fcceffb70349_final.mp4",
        "characters": ["老猫头鹰", "小狐狸"],
    },
    {
        "id": "lantern-keeper",
        "task_id": "f6731663-21ed-45e8-9c30-7e743ad2fc7b",
        "title": "提灯人",
        "meta": "8 镜头 · 720p · 已配音",
        "video": "/videos/f6731663-21ed-45e8-9c30-7e743ad2fc7b_tidengman_final.mp4",
        "characters": ["小满", "小鹿", "山谷老人"],
    },
    {
        "id": "rooftop-signal",
        "task_id": "ede1f009-f1dc-4e7b-aaab-d480b82a6354",
        "title": "天台上的信号",
        "meta": "16 镜头 · 720p",
        "video": "/videos/155d84f0-d595-4e3a-a48e-7cebaa50e579_final.mp4",
        "characters": ["少年", "女孩"],
    },
    {
        "id": "umbrella-mender",
        "task_id": "718d2afa-2016-4f71-bd94-37ed494634d0",
        "title": "修伞匠",
        "meta": "28 镜头 · 720p",
        "video": "/videos/718d2afa-2016-4f71-bd94-37ed494634d0_final.mp4",
        "characters": ["老修伞匠", "小女孩"],
    },
    {
        "id": "squirrel-qiqi",
        "task_id": "bf7d7db7-b545-4df9-889f-91a38afd6a20",
        "title": "松鼠奇奇",
        "meta": "6 镜头 · 720p · 字幕版",
        "video": "/videos/bf7d7db7-b545-4df9-889f-91a38afd6a20_squirrel_final_subtitled.mp4",
        "characters": ["松鼠奇奇"],
    },
]


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return normalized or "asset"


def resolve_output_path(value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value).replace("\\", "/"))
    if candidate.is_absolute() and candidate.exists():
        return candidate
    rooted = ROOT / candidate
    if rooted.exists():
        return rooted
    basename = candidate.name
    for folder in ("keyframes", "characters"):
        fallback = ROOT / "outputs" / folder / basename
        if fallback.exists():
            return fallback
    return None


def export_webp(source: Path | None, destination: Path) -> bool:
    if not source or not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((960, 960), Image.Resampling.LANCZOS)
        image.save(destination, "WEBP", quality=78, method=6)
    return True


def story_text(task: dict) -> str:
    existing = str(task.get("script_synopsis") or task.get("raw_script") or "").strip()
    if existing:
        return existing
    plots = [
        str(item.get("Plot") or "").strip()
        for item in task.get("sub_scripts", {}).get("Sub-Script", {}).values()
    ]
    return "\n\n".join(plot for plot in plots if plot)


def remove_internal_fields(value):
    if isinstance(value, dict):
        return {
            key: remove_internal_fields(item)
            for key, item in value.items()
            if "chain-of-thought" not in key.lower()
            and "internal reasoning" not in key.lower()
        }
    if isinstance(value, list):
        return [remove_internal_fields(item) for item in value]
    return value


def sanitize_task(case: dict, source: dict) -> dict:
    task = copy.deepcopy(source)
    task["status"] = "done"
    task["progress"] = 100
    task["script_synopsis"] = story_text(task)
    task["raw_script"] = task.get("raw_script") or task["script_synopsis"]
    task["characters"] = task.get("characters") or case["characters"]
    task["final_video_url"] = case["video"]

    allowed_logs = []
    for entry in task.get("logs", []):
        text = str(entry)
        if any(token in text.lower() for token in ("api key", "traceback", "retry", "exception")):
            continue
        allowed_logs.append(text)
    task["logs"] = allowed_logs

    character_refs = {}
    for index, (name, value) in enumerate((task.get("character_refs") or {}).items()):
        destination = ASSETS_DIR / "characters" / f"{case['id']}-{index + 1}.webp"
        if export_webp(resolve_output_path(value), destination):
            character_refs[name] = f"/case-assets/characters/{destination.name}"
    task["character_refs"] = character_refs

    shot_index = 0
    for sub_name, sub_script in list((task.get("shots") or {}).items()):
        for scene_name, scene in list((sub_script or {}).items()):
            shots = (scene or {}).get("Shot", {})
            for shot_name, shot in list(shots.items()):
                shot_index += 1
                source_path = resolve_output_path(
                    shot.get("keyframe_path")
                    or shot.get("enhanced_keyframe_path")
                    or shot.get("keyframe_url")
                )
                if not source_path:
                    parts = [sub_name, scene_name, shot_name]
                    suffix = "_".join(re.sub(r"[^0-9A-Za-z-]+", "_", part).strip("_") for part in parts)
                    source_path = resolve_output_path(
                        f"outputs/keyframes/{case['task_id']}_{suffix}.png"
                    )
                destination = ASSETS_DIR / "keyframes" / f"{case['id']}-{shot_index:02d}.webp"
                if export_webp(source_path, destination):
                    shot["keyframe_url"] = f"/case-assets/keyframes/{destination.name}"
                    shot["keyframe_status"] = "done"
                else:
                    shots.pop(shot_name, None)
                    continue
                shot["video_url"] = ""
                for key in list(shot):
                    lowered = key.lower()
                    if (
                        lowered.endswith("_path")
                        or (lowered.endswith("_url") and lowered not in {"keyframe_url", "video_url"})
                        or "error" in lowered
                        or "reflection" in lowered
                        or lowered in {"audio_files", "video_prompt", "raw_response"}
                    ):
                        shot.pop(key, None)

            if not shots:
                sub_script.pop(scene_name, None)

    for sub_name, sub_script in (task.get("shots") or {}).items():
        scene_map = task.setdefault("scenes", {}).setdefault(sub_name, {}).setdefault("Scene", {})
        for scene_name in sub_script:
            scene_map.setdefault(scene_name, {
                "Plot": scene_name.replace("_", " ").title(),
                "Scene Description": scene_name.replace("_", " ").title(),
                "Emotional Tone": "",
            })

    return remove_internal_fields({
        "status": task["status"],
        "progress": task["progress"],
        "logs": task["logs"],
        "sub_scripts": task.get("sub_scripts", {}),
        "scenes": task.get("scenes", {}),
        "shots": task.get("shots", {}),
        "character_refs": task["character_refs"],
        "cost": task.get("cost", {}),
        "script_synopsis": task["script_synopsis"],
        "raw_script": task["raw_script"],
        "characters": task["characters"],
        "final_video_url": task["final_video_url"],
    })


def main() -> None:
    with TASKS_PATH.open("r", encoding="utf-8") as handle:
        tasks = json.load(handle)

    snapshots = []
    for case in CASES:
        if case["task_id"] not in tasks:
            raise KeyError(f"Missing task: {case['task_id']}")
        task = sanitize_task(case, tasks[case["task_id"]])
        shot_count = sum(
            len((scene or {}).get("Shot", {}))
            for sub_script in (task.get("shots") or {}).values()
            for scene in (sub_script or {}).values()
        )
        snapshots.append({
            "case": {
                **case,
                "meta": f"{shot_count} 镜头 · 720p" + (" · 已配音" if "已配音" in case["meta"] else ""),
                "shot_count": shot_count,
                "preview": next(
                    (
                        shot.get("keyframe_url")
                        for sub_script in (task.get("shots") or {}).values()
                        for scene in (sub_script or {}).values()
                        for shot in (scene or {}).get("Shot", {}).values()
                        if shot.get("keyframe_url")
                    ),
                    "",
                ),
            },
            "task": task,
            "script": task["script_synopsis"],
            "characters": task["characters"],
        })

    SNAPSHOTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOTS_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"cases": snapshots}, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"Built {len(snapshots)} cases at {SNAPSHOTS_PATH}")


if __name__ == "__main__":
    main()
