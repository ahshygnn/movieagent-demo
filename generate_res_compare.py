"""分辨率对比：同一 prompt，draft vs final，并发生成。"""
import os, time
from concurrent.futures import ThreadPoolExecutor
import requests
from generation.image import _call_api, _no_proxy
import config

SAVE_DIR = "outputs/characters"
PROMPT = (
    "Family-friendly animated film scene, Disney/Pixar style, safe for all ages. "
    "一位慈祥的白胡子老人,身穿朴素的灰色布衣,背微微佝偻,眼神睿智而温和,脸上刻着岁月的皱纹。"
    "他站在昏黄的木屋灯光下,身后隐约是简朴的屋内陈设和温暖的烛光。"
    "3D 动画电影风格,皮克斯质感,温暖的侧光,角色半身居中,电影感构图,画面精致细腻。"
)

JOBS = [
    {"label": "draft_2560x1440", "size": config.KEYFRAME_SIZE_DRAFT, "filename": "compare_draft.png"},
    {"label": "final_2848x1600", "size": config.KEYFRAME_SIZE_FINAL, "filename": "compare_final.png"},
]

def run(job):
    t0 = time.time()
    with _no_proxy():
        url = _call_api(PROMPT, [], size=job["size"])
    with _no_proxy():
        resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()
    path = os.path.join(SAVE_DIR, job["filename"])
    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256*1024):
            if chunk: f.write(chunk)
    elapsed = round(time.time() - t0, 1)
    print(f"  [{job['label']}] {elapsed}s → {path}")
    return path, elapsed

os.makedirs(SAVE_DIR, exist_ok=True)
print("并发生成两张对比图...")
t0 = time.time()
with ThreadPoolExecutor(max_workers=2) as ex:
    results = list(ex.map(run, JOBS))
print(f"\n总耗时: {time.time()-t0:.1f}s")
for path, elapsed in results:
    size_kb = os.path.getsize(path) // 1024
    print(f"  {path}  ({size_kb} KB)")
