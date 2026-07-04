"""
《提灯人》三个角色定妆图生成脚本。
无参考图注入，从零生成，存到 outputs/characters/。
"""
import os
import sys
import time
import requests

from generation.image import _call_api, _no_proxy, _SAFE_PREFIX
import config

SAVE_DIR = "outputs/characters"

CHARACTERS = [
    {
        "filename": "xiaoman.png",
        "name": "小满",
        "prompt": (
            "一个约十岁的东方女孩,圆脸大眼,扎着两个小辫子,穿着深蓝色带补丁的旧披风,"
            "手里提着一盏散发暖黄色光芒的老式灯笼,表情温柔而坚定。"
            "她站在朦胧的山谷雾气中,身后隐约可见蜿蜒的山道和几盏散发微光的灯。"
            "3D 动画电影风格,皮克斯质感,柔和的暖色调光照,角色全身居中,电影感构图,画面精致细腻。"
        ),
    },
    {
        "filename": "xiaolu.png",
        "name": "小鹿",
        "prompt": (
            "一只小巧的幼鹿,雪白柔软的皮毛,一对鹿角上缠绕着淡金色的微光,"
            "大眼睛湿润有灵性,身形柔弱惹人怜爱。"
            "它站在朦胧的山间雾气中,周围有柔和梦幻的光晕和隐约的树影。"
            "3D 动画电影风格,皮克斯质感,柔和梦幻的光照,动物全身居中,电影感构图,画面精致细腻。"
        ),
    },
    {
        "filename": "laoren.png",
        "name": "山谷老人",
        "prompt": (
            "一位慈祥的白胡子老人,身穿朴素的灰色布衣,背微微佝偻,眼神睿智而温和,脸上刻着岁月的皱纹。"
            "他站在昏黄的木屋灯光下,身后隐约是简朴的屋内陈设和温暖的烛光。"
            "3D 动画电影风格,皮克斯质感,温暖的侧光,角色半身居中,电影感构图,画面精致细腻。"
        ),
    },
]


def generate_one(name: str, filename: str, prompt: str) -> dict:
    save_path = os.path.join(SAVE_DIR, filename)
    t0 = time.time()

    full_prompt = _SAFE_PREFIX + prompt

    # 重试策略：第1次完整prompt，第2次简化
    attempts = [full_prompt, _SAFE_PREFIX + prompt[:200]]
    last_err = None
    image_url = None
    for i, p in enumerate(attempts):
        try:
            with _no_proxy():
                image_url = _call_api(p, [], size=config.KEYFRAME_SIZE_FINAL)
            break
        except Exception as e:
            last_err = e
            print(f"  [{name}] 尝试 {i+1}/2 失败: {e}")
            if i < len(attempts) - 1:
                time.sleep(3)

    if not image_url:
        raise RuntimeError(f"[{name}] 生成失败: {last_err}")

    # 下载保存（带重试）
    for dl in range(3):
        try:
            with _no_proxy():
                resp = requests.get(image_url, timeout=120, stream=True)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            print(f"  [{name}] 下载重试 {dl+1}/3: {e}")
            time.sleep(3)
    else:
        raise RuntimeError(f"[{name}] 下载失败")

    elapsed = round(time.time() - t0, 1)
    print(f"  [{name}] 完成，耗时 {elapsed}s → {save_path}")
    return {"name": name, "path": save_path, "elapsed": elapsed}


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    def _run(char):
        print(f"生成 {char['name']}（{char['filename']}）...")
        try:
            return generate_one(char["name"], char["filename"], char["prompt"])
        except Exception as e:
            print(f"  [{char['name']}] ERROR: {e}")
            return {"name": char["name"], "path": None, "elapsed": None, "error": str(e)}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=len(CHARACTERS)) as executor:
        future_map = {executor.submit(_run, char): char for char in CHARACTERS}
        for future in as_completed(future_map):
            results.append(future.result())
    print(f"\n并发总耗时: {time.time() - t0:.1f}s")

    print("\n" + "=" * 50)
    print("生成完成")
    print("=" * 50)
    for r in results:
        if r.get("path"):
            print(f"  {r['name']:<8} {r['elapsed']}s  →  {r['path']}")
        else:
            print(f"  {r['name']:<8} 失败: {r.get('error')}")


if __name__ == "__main__":
    main()
