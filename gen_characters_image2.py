"""
参数化的人物定妆图生成 CLI（Seedream 5.0 / 火山方舟）。

从剧本 + 角色名列表，自动提取每人外貌并生成全身正面定妆照到 outputs/characters/。
与老脚本 generate_characters.py（写死角色 + Seedream）并行，互不影响。

用法示例：
  python gen_characters_image2.py --characters "小满,引路鹿,山谷老人" \
      --script-file story.txt
  python gen_characters_image2.py --characters "小满 引路鹿" \
      --script "雾气缭绕的山谷里，提灯女孩小满遇见了引路鹿……"
"""
import argparse
import sys

from generation.character import generate_character_references


def _parse_characters(raw: str) -> list[str]:
    # 支持逗号或空白分隔
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split()]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="生成人物全身正面定妆照（Seedream 5.0）。")
    parser.add_argument("--characters", required=True, help="角色名，逗号或空格分隔")
    parser.add_argument("--script", default="", help="剧本文本（直接传字符串）")
    parser.add_argument("--script-file", default="", help="剧本文件路径（UTF-8）")
    parser.add_argument("--concurrency", type=int, default=None, help="并发数，默认取 KEYFRAME_MAX_CONCURRENCY")
    args = parser.parse_args()

    characters = _parse_characters(args.characters)
    if not characters:
        print("未解析到角色名。", flush=True)
        return 2

    script = args.script
    if args.script_file:
        with open(args.script_file, "r", encoding="utf-8") as f:
            script = f.read()
    if not script.strip():
        print("警告：未提供剧本，外貌描述将仅凭角色名推断，质量可能下降。", flush=True)

    print(f"角色：{characters}", flush=True)
    out = generate_character_references(script, characters, concurrency=args.concurrency)

    refs = out.get("character_refs") or {}
    errors = out.get("errors") or {}
    print("\n" + "=" * 50, flush=True)
    print("生成完成", flush=True)
    print("=" * 50, flush=True)
    for name in characters:
        if name in refs:
            print(f"  {name:<10} → {refs[name]}", flush=True)
        else:
            print(f"  {name:<10} 失败: {errors.get(name)}", flush=True)
    return 0 if refs else 1


if __name__ == "__main__":
    sys.exit(main())
