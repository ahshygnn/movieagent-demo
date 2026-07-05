CHARACTER_DESIGNER_PROMPT = """You are a character designer for an animated film. Given a full movie script (synopsis) and a list of character names, produce a concise, production-ready VISUAL description for EACH character, written specifically to drive a text-to-image model that renders a full-body front-facing character reference sheet.

For every character you must output two English fields:

1. **Appearance** — the character's stable visual look only. Cover, as applicable:
   - Approximate age and gender, or species for non-human characters.
   - Face shape, notable facial features, eyes, hairstyle and hair color.
   - Body type / build and rough height impression.
   - Signature clothing / outfit and its color palette; distinctive accessories or hand-held props.
   - Overall demeanor or temperament conveyed through look (e.g. gentle, stern, mischievous).
   Describe only what is visually stable across the whole story. Do NOT mention plot events, specific shots, camera work, or transient actions.

2. **Background** — a short English description of an environment that fits the story's world (e.g. a misty mountain valley, a quiet forest library), suitable as the backdrop for this character's reference portrait. Keep it simple and uncluttered so it never competes with the character.

Rules:
- Write both fields in natural English, even if the script is in another language.
- The character names in your output MUST match the provided names EXACTLY (same spelling, same language). Do not add, drop, translate, or modify any name.
- Include an entry for every provided name and no others.
- Keep each field tight and concrete (roughly one to three sentences); avoid vague filler.

Output ONLY one valid JSON object in exactly this shape:

{
    "Characters": {
        "<character name>": {
            "Appearance": "<English appearance description>",
            "Background": "<English environment description fitting the story>"
        }
    }
}
"""

from agents.base_agent import BaseAgent


def _normalize_designer_result(result: dict, characters: list[str]) -> dict:
    """确保返回结构规整：顶层有 Characters，且每个角色都有 Appearance/Background 两个字段。"""
    chars = {}
    raw = (result or {}).get("Characters")
    if isinstance(raw, dict):
        chars = raw
    normalized = {}
    for name in characters:
        entry = chars.get(name) if isinstance(chars.get(name), dict) else {}
        normalized[name] = {
            "Appearance": str(entry.get("Appearance", "") or "").strip(),
            "Background": str(entry.get("Background", "") or "").strip(),
        }
    return {"Characters": normalized}


def run_character_designer_agent(movie_script: str, characters: list[str]) -> dict:
    """
    读剧本 + 角色名列表，为每个角色生成英文外貌(Appearance)与英文剧本环境背景(Background)。
    返回 {"result": {"Characters": {...}}, "usage": {...}}，结构对齐其它 agent。
    """
    agent = BaseAgent(system_prompt=CHARACTER_DESIGNER_PROMPT, temp=0.7)
    names = ", ".join(characters)
    query = f"""Design the characters for the following story.

- Character names (use these EXACT names as JSON keys, do not change them): {names}
- Movie script / synopsis:
\"\"\"
{movie_script}
\"\"\"

Return the JSON object described in your instructions, with one entry per character name above."""
    result = agent(query, parse=True)
    result = _normalize_designer_result(result, characters)
    return {"result": result, "usage": agent.get_usage()}
