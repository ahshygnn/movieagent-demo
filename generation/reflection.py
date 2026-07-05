"""
关键帧质量反思-审核模块（视觉理解模型，走一展 OpenAI 兼容多模态接口）。

对已生成的关键帧，从四个维度审核：常理/解剖合理性(plausibility)、人物一致性
(character_consistency)、画风一致性(style_consistency)、画面内容匹配(content_match)。
每维给 score(1-5，辅助质量分) 与 verdict(pass/minor/severe/na)；任一维 severe 即
needs_regeneration=True，供 generate_keyframe 带反馈重生成。

审核模型：config.REFLECTION_MODEL（默认 gemini-3-flash-preview），
与关键帧生成(Seedream/Ark)、角色图生成(image-2)三者相互独立。
"""
import base64
import io
import json
import time

from openai import OpenAI

import config
from agents.base_agent import _strip_json_fence, _json_object_slice


REFLECTION_SYSTEM_PROMPT = """You are a strict QA reviewer for animated-film keyframes. You will receive: one [KEYFRAME UNDER REVIEW], zero or more [CHARACTER REFERENCE] images (each labeled with the character's name and an auxiliary English appearance description), and this shot's [TARGET ART STYLE] and [SCENE DESCRIPTION]. Judge ONLY from what is visible in the images. Do not imagine content that is not there, and do not relax standards because an image looks appealing.

Scoring dimensions - give each a score 1-5 (auxiliary quality signal) and a verdict based on that dimension's own criteria:
1. plausibility: severe = clear anatomical/physical defects (missing body parts such as no lower body, extra/broken limbs, deformed hands/face/eyes, floating/anti-gravity, the same character duplicated); minor = only slight garbled text on a background sign or minor blemishes; pass = none.
2. character_consistency: judge whether it is the same individual, using the [CHARACTER REFERENCE] image as the PRIMARY basis and its English appearance description as auxiliary. severe = clearly a different individual (wrong species, wrong main outfit color, missing signature accessory such as glasses); minor = essentially the same with minor detail differences; pass = consistent. If the character is too small/distant/occluded to identify, do NOT mark severe - give pass or na. If this shot has no character reference at all, output verdict="na", score=null.
3. style_consistency: judge ONLY painting medium/technique/palette. severe = wrong medium overall (target is watercolor but rendered as a realistic photo / 3D render / clean cel-shaded flat color); minor = broadly matches but slightly off in saturation/brushwork; pass = matches the target style.
4. content_match (relaxed threshold): severe = the main subject or setting is entirely wrong (e.g. description says an owl in a library but the image shows a fox in a meadow); otherwise, if the core subject is present, it is minor or pass. Do NOT mark severe merely because the description was not reproduced word-for-word.

fix_instructions: if there is any severe or minor issue, write ONE short, concrete, actionable English instruction for the text-to-image model to repaint (focus on the most serious issue, e.g. "Show the full body including legs and feet; render in loose hand-painted watercolor with visible paper texture, not clean cel-shading"); if everything passes, use an empty string.

Output EXACTLY ONE JSON object (no markdown, comments, or extra text), schema:
{
 "plausibility":{"score":1-5|null,"verdict":"pass|minor|severe|na","reason":"short English reason"},
 "character_consistency":{"score":1-5|null,"verdict":"pass|minor|severe|na","reason":"short English reason"},
 "style_consistency":{"score":1-5|null,"verdict":"pass|minor|severe|na","reason":"short English reason"},
 "content_match":{"score":1-5|null,"verdict":"pass|minor|severe|na","reason":"short English reason"},
 "fix_instructions":"English one-liner or empty string"
}"""


DIMENSIONS = ("plausibility", "character_consistency", "style_consistency", "content_match")


def _client() -> OpenAI:
    return OpenAI(
        api_key=config.YIZHAN_API_KEY,
        base_url=config.YIZHAN_BASE_URL.rstrip("/"),
    )


def _downscale_data_url(path: str, max_side: int | None = None) -> str:
    """把本地图片等比缩放到最长边 <= max_side，重新编码为 PNG 的 base64 data URL，控制审核 token/延迟。"""
    from PIL import Image

    max_side = int(max_side or config.REFLECTION_MAX_SIDE or 768)
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, float(max_side) / float(max(w, h)))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _image_part(path: str) -> dict:
    return {"type": "image_url", "image_url": {"url": _downscale_data_url(path)}}


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _norm_dim(raw: dict | None) -> dict:
    """规整单个维度的返回，容错缺字段/非法值。"""
    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict", "")).strip().lower()
    if verdict not in ("pass", "minor", "severe", "na"):
        verdict = "na"
    score = raw.get("score")
    if not isinstance(score, (int, float)):
        score = None
    reason = str(raw.get("reason", "") or "").strip()
    return {"score": score, "verdict": verdict, "reason": reason}


def _aggregate(dimensions: dict) -> tuple[str, bool, int, int]:
    """由四维 verdict 汇总 review_status / needs_regeneration / severe_count / total_score。"""
    verdicts = [dimensions[d]["verdict"] for d in DIMENSIONS]
    severe_count = sum(1 for v in verdicts if v == "severe")
    minor_count = sum(1 for v in verdicts if v == "minor")
    total_score = sum(
        int(dimensions[d]["score"])
        for d in DIMENSIONS
        if isinstance(dimensions[d]["score"], (int, float))
    )
    needs_regeneration = severe_count > 0
    if severe_count > 0:
        status = "fail"
    elif minor_count > 0:
        status = "warn"
    else:
        status = "pass"
    return status, needs_regeneration, severe_count, total_score


def _fail_open_report(error: str) -> dict:
    """审核调用/解析失败时的兜底报告：fail-open 视为通过，fail-closed 则要求重生成。"""
    fail_open = bool(config.REFLECTION_FAIL_OPEN)
    empty_dims = {d: {"score": None, "verdict": "na", "reason": "reviewer unavailable"} for d in DIMENSIONS}
    return {
        "review_status": "pass" if fail_open else "fail",
        "needs_regeneration": (not fail_open),
        "dimensions": empty_dims,
        "fix_instructions": "",
        "severe_count": 0,
        "total_score": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "error": error,
    }


def review_keyframe(
    image_path: str,
    *,
    visual_style: str,
    plot_description: str,
    character_refs: dict | None = None,
    appearance_texts: dict | None = None,
) -> dict:
    """
    审核单张关键帧，返回：
      {review_status, needs_regeneration, dimensions, fix_instructions,
       severe_count, total_score, usage, error}
    审核失败按 config.REFLECTION_FAIL_OPEN 兜底（默认放行）。
    """
    character_refs = character_refs or {}
    appearance_texts = appearance_texts or {}

    try:
        content: list = [
            _text_part(
                f"[TARGET ART STYLE]: {visual_style}\n"
                f"[SCENE DESCRIPTION]: {plot_description}\n"
                "Below are the character reference(s) (if any) followed by the keyframe "
                "under review. Output JSON per the schema."
            )
        ]
        for name, ref_path in character_refs.items():
            appearance = str(appearance_texts.get(name, "") or "").strip()
            label = f"[CHARACTER REFERENCE - {name}]"
            if appearance:
                label += f" appearance: {appearance}"
            content.append(_text_part(label))
            content.append(_image_part(ref_path))

        content.append(_text_part("[KEYFRAME UNDER REVIEW]:"))
        content.append(_image_part(image_path))

        messages = [
            {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        resp = _client().chat.completions.create(
            model=config.REFLECTION_MODEL,
            messages=messages,
            temperature=0,
            max_tokens=800,
            timeout=90,
        )
        usage = {
            "input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
        }
        raw_text = resp.choices[0].message.content or ""
        parsed = json.loads(_json_object_slice(_strip_json_fence(raw_text)))
    except Exception as e:
        return _fail_open_report(f"{type(e).__name__}: {e}")

    dimensions = {d: _norm_dim(parsed.get(d)) for d in DIMENSIONS}
    # 无参考图时人物一致性强制 na（不计入阻断）
    if not character_refs:
        dimensions["character_consistency"] = {"score": None, "verdict": "na", "reason": "no character reference"}

    status, needs_regen, severe_count, total_score = _aggregate(dimensions)
    fix = str(parsed.get("fix_instructions", "") or "").strip()

    return {
        "review_status": status,
        "needs_regeneration": needs_regen,
        "dimensions": dimensions,
        "fix_instructions": fix,
        "severe_count": severe_count,
        "total_score": total_score,
        "usage": usage,
        "error": None,
    }


if __name__ == "__main__":
    # 冒烟：python -m generation.reflection <keyframe.png> [ref_name=ref_path ...]
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m generation.reflection <keyframe.png> [name=refpath ...]")
        raise SystemExit(1)
    kf = sys.argv[1]
    refs = {}
    for arg in sys.argv[2:]:
        if "=" in arg:
            n, p = arg.split("=", 1)
            refs[n] = p
    t0 = time.time()
    report = review_keyframe(
        kf,
        visual_style=config.VISUAL_STYLE,
        plot_description="(smoke test, no plot)",
        character_refs=refs,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"elapsed {time.time() - t0:.1f}s")
