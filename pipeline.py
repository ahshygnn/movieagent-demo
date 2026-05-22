import sys
import time
import uuid
import json
from pathlib import Path
from agents.director import run_director_agent
from agents.scene import run_scene_agent
from agents.shot import run_shot_agent

# 内存任务存储（demo 阶段不需要数据库）
tasks = {}
TASKS_FILE = Path("outputs/tasks.json")


def save_tasks():
    try:
        TASKS_FILE.parent.mkdir(exist_ok=True)
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存 tasks 失败: {e}")


def load_tasks():
    if TASKS_FILE.exists():
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                tasks.update(data)
            print(f"已恢复 {len(data)} 个历史任务")
        except Exception as e:
            print(f"加载 tasks 失败: {e}")


load_tasks()  # 模块加载时执行


def create_task() -> str:
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "logs": [],
        "sub_scripts": None,
        "scenes": {},
        "shots": {},
        "character_refs": {},
        "cost": {
            "input_tokens": 0,
            "output_tokens": 0
        }
    }
    return task_id


def _log(task_id: str, message: str):
    tasks[task_id]["logs"].append(message)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    if enc.lower() in ("utf-8", "utf8"):
        print(message)
    else:
        # Windows 控制台常见 GBK：无法编码 emoji 时用 replace，避免后台任务整段失败
        print(message.encode(enc, errors="replace").decode(enc, errors="replace"))
    save_tasks()  # 每次更新都保存


def _add_cost(task_id: str, usage: dict):
    tasks[task_id]["cost"]["input_tokens"] += usage.get("input_tokens", 0)
    tasks[task_id]["cost"]["output_tokens"] += usage.get("output_tokens", 0)
    save_tasks()  # 每次更新都保存


def run_full_pipeline(task_id: str, movie_script: str, characters: list):
    """
    完整三步规划流水线，在后台异步执行。
    Step 1: Director Agent  → sub_scripts
    Step 2: Scene Agent     → scenes（每个 sub_script 调用一次）
    Step 3: Shot Agent      → shots（每个 scene 调用一次）
    """
    try:
        # ── Step 1: Director Agent ──────────────────────────────
        tasks[task_id]["status"] = "director_running"
        _log(task_id, "🎬 Director Agent 开始分析剧本...")

        director_out = run_director_agent(movie_script, characters)
        tasks[task_id]["sub_scripts"] = director_out["result"]
        _add_cost(task_id, director_out["usage"])

        sub_script_count = len(director_out["result"].get("Sub-Script", {}))
        tasks[task_id]["progress"] = 20
        _log(task_id, f"✅ Director Agent 完成，生成 {sub_script_count} 个子剧本")

        # ── Step 2: Scene Agent ─────────────────────────────────
        tasks[task_id]["status"] = "scene_running"
        relationships = director_out["result"].get("Relationships", {})
        sub_scripts = director_out["result"].get("Sub-Script", {})

        for i, (ss_name, ss_data) in enumerate(sub_scripts.items()):
            _log(task_id, f"🎭 Scene Agent 处理 {ss_name}...")
            scene_out = run_scene_agent(ss_data["Plot"], relationships)
            tasks[task_id]["scenes"][ss_name] = scene_out["result"]
            _add_cost(task_id, scene_out["usage"])
            tasks[task_id]["progress"] = 20 + int(30 * (i + 1) / len(sub_scripts))

        _log(task_id, "✅ Scene Agent 完成")

        # ── Step 3: Shot Agent ──────────────────────────────────
        tasks[task_id]["status"] = "shot_running"
        all_scenes = [
            (ss_name, scene_name, scene_data)
            for ss_name, scene_annotation in tasks[task_id]["scenes"].items()
            for scene_name, scene_data in scene_annotation.get("Scene", {}).items()
        ]
        total = max(len(all_scenes), 1)

        for idx, (ss_name, scene_name, scene_data) in enumerate(all_scenes):
            _log(task_id, f"📽️ Shot Agent 处理 {ss_name} → {scene_name}...")
            shot_out = run_shot_agent(scene_data)

            if ss_name not in tasks[task_id]["shots"]:
                tasks[task_id]["shots"][ss_name] = {}
            tasks[task_id]["shots"][ss_name][scene_name] = shot_out["result"]
            _add_cost(task_id, shot_out["usage"])
            tasks[task_id]["progress"] = 50 + int(45 * (idx + 1) / total)

            time.sleep(2)  # 避免触发限速

        # ── 完成 ────────────────────────────────────────────────
        tasks[task_id]["status"] = "done"
        tasks[task_id]["progress"] = 100
        _log(task_id, "🎉 规划阶段全部完成！现在可以开始生成关键帧。")

    except Exception as e:
        tasks[task_id]["status"] = "error"
        _log(task_id, f"❌ 出错了：{str(e)}")
