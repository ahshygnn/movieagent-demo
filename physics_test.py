"""
物理引导对照测试 — 文生视频 (Seedance)
生成 20 个视频：5 动作 × 2 版本(before/after) × 2 次
用法：python physics_test.py
"""
import contextlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
OUT_DIR = Path("outputs/physics_test")
MODEL = "doubao-seedance-1-0-pro-fast-251015"
DURATION = 5
RESOLUTION = "720p"
MAX_WORKERS = 2
POLL_INTERVAL = 8
POLL_TIMEOUT = 360

PROMPTS = {
    "rabbit_before": "A rabbit jumps onto a big rock.",
    "child_before": "A child runs.",
    "box_before": "A person lifts a large box.",
    "bike_before": "A bicycle stops.",
    "water_before": "Water is poured into a bowl.",
    "rabbit_after": (
        "A rabbit crouches, then pushes off the ground with its hind legs, "
        "its body arcing upward and forward, and lands on top of a big rock. "
        "Natural gravity, clear upward jumping trajectory."
    ),
    "child_after": (
        "A child, facing toward the camera, runs forward across an open field, "
        "arms and legs swinging naturally in coordinated rhythm, moving clearly "
        "from far to near. Correct forward-facing movement direction."
    ),
    "box_after": (
        "A person bends down, grips a large heavy box with visible effort, "
        "muscles straining, and slowly lifts it up with the weight pulling their "
        "arms down. The box moves heavily with real weight and momentum."
    ),
    "bike_after": (
        "A bicycle moving at speed gradually slows down, decelerating smoothly "
        "as the brakes engage, and comes to a natural stop with slight forward "
        "momentum before settling. Smooth deceleration, natural inertia."
    ),
    "water_after": (
        "A hand tilts a cup, and water flows downward in a continuous stream due "
        "to gravity, filling the bowl and rippling on the surface. Realistic fluid "
        "motion, water falling straight down under gravity."
    ),
}


@contextlib.contextmanager
def _no_proxy():
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _headers():
    return {
        "Authorization": f"Bearer {config.ARK_API_KEY}",
        "Content-Type": "application/json",
    }


def submit_t2v(prompt: str) -> str:
    import requests
    full_prompt = f"{prompt} --resolution {RESOLUTION} --duration {DURATION} --watermark false"
    payload = {
        "model": MODEL,
        "content": [{"type": "text", "text": full_prompt}],
    }
    with _no_proxy():
        resp = requests.post(
            f"{ARK_BASE}/contents/generations/tasks",
            json=payload,
            headers=_headers(),
            timeout=60,
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"提交失败 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise RuntimeError(f"未返回 task_id: {data}")
    return task_id


def poll(task_id: str) -> dict:
    import requests
    waited = 0
    while waited < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        with _no_proxy():
            resp = requests.get(
                f"{ARK_BASE}/contents/generations/tasks/{task_id}",
                headers=_headers(),
                timeout=30,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"状态查询失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        status = data.get("status")
        if status == "succeeded":
            content = data.get("content", {})
            video_url = content.get("video_url") if isinstance(content, dict) else None
            if not video_url:
                raise RuntimeError(f"succeeded 但无 video_url: {data}")
            return {"status": "succeeded", "video_url": video_url}
        elif status == "failed":
            reason = data.get("error", {}).get("message", "未知错误")
            return {"status": "failed", "reason": reason}
        # queued / running，继续等待
    return {"status": "failed", "reason": f"轮询超时（{POLL_TIMEOUT}s）"}


def download_video(video_url: str, local_path: Path):
    import requests
    with _no_proxy():
        r = requests.get(video_url, timeout=180, stream=True)
    r.raise_for_status()
    with open(local_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if chunk:
                f.write(chunk)


def generate_one(name: str, run: int, prompt: str) -> dict:
    filename = f"test_{name}_{run}.mp4"
    local_path = OUT_DIR / filename
    start = time.time()

    # 已存在且有效则跳过
    if local_path.exists() and local_path.stat().st_size > 4096:
        print(f"  ⏭  {filename} 已存在，跳过")
        return {"name": name, "run": run, "file": filename,
                "path": str(local_path), "status": "cached", "elapsed": 0.0}

    print(f"  ▶  提交 {filename} ...")
    try:
        task_id = submit_t2v(prompt)
        print(f"     {filename} → task_id={task_id}，等待完成...")
        result = poll(task_id)
        if result["status"] != "succeeded":
            reason = result.get("reason", "未知")
            print(f"  ❌ {filename} 失败: {reason}")
            return {"name": name, "run": run, "file": filename,
                    "status": "failed", "reason": reason,
                    "elapsed": round(time.time() - start, 1)}
        download_video(result["video_url"], local_path)
        elapsed = round(time.time() - start, 1)
        size_kb = local_path.stat().st_size // 1024
        print(f"  ✅ {filename}  {size_kb} KB  {elapsed}s")
        return {"name": name, "run": run, "file": filename,
                "path": str(local_path), "status": "ok", "elapsed": elapsed}
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        print(f"  ❌ {filename} 异常: {e}")
        return {"name": name, "run": run, "file": filename,
                "status": "failed", "reason": str(e), "elapsed": elapsed}


def main():
    import io
    # Windows 控制台 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jobs = [(name, run, prompt)
            for name, prompt in PROMPTS.items()
            for run in [1, 2]]

    print(f"物理引导对照测试 — 共 {len(jobs)} 个视频，并发 {MAX_WORKERS} 路")
    print(f"模型: {MODEL}  分辨率: {RESOLUTION}  时长: {DURATION}s")
    print(f"输出目录: {OUT_DIR.resolve()}\n")

    total_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(generate_one, name, run, prompt): (name, run)
            for name, run, prompt in jobs
        }
        for future in as_completed(futures):
            results.append(future.result())

    total_elapsed = time.time() - total_start
    ok = [r for r in results if r["status"] in ("ok", "cached")]
    failed = [r for r in results if r["status"] == "failed"]

    print(f"\n{'='*60}")
    print(f"完成！成功 {len(ok)}/{len(jobs)}  失败 {len(failed)}")
    print(f"总耗时: {total_elapsed/60:.1f} 分钟")

    if failed:
        print("\n失败明细:")
        for r in failed:
            print(f"  ❌ {r['file']}: {r.get('reason', '未知')}")

    print("\n文件清单:")
    results.sort(key=lambda r: r["file"])
    for r in results:
        p = OUT_DIR / r["file"]
        size = f"{p.stat().st_size//1024} KB" if p.exists() else "N/A"
        print(f"  {r['file']:45s} [{r['status']:6s}]  {size}")

    # 输出对照表框架
    actions = [
        ("兔子跳上石头", "rabbit"),
        ("小孩向前奔跑", "child"),
        ("人搬起大箱子", "box"),
        ("自行车刹车停下", "bike"),
        ("倒水进碗", "water"),
    ]
    print("\n\n对照表（视频内容判断请自行填写）:")
    print(f"{'动作':<14} {'改前-1':^30} {'改前-2':^30} {'改后-1':^30} {'改后-2':^30}")
    print("-" * 126)
    for label, key in actions:
        b1 = f"test_{key}_before_1.mp4"
        b2 = f"test_{key}_before_2.mp4"
        a1 = f"test_{key}_after_1.mp4"
        a2 = f"test_{key}_after_2.mp4"
        print(f"{label:<14} {b1:^30} {b2:^30} {a1:^30} {a2:^30}")


if __name__ == "__main__":
    main()
