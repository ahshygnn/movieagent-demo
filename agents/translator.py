from agents.base_agent import BaseAgent

_SYSTEM = (
    "You are a professional translator. Translate the given movie synopsis into fluent, cinematic English. "
    "Preserve all character names exactly as provided — do not translate or romanize them. "
    "Keep the narrative structure, emotional tone, and story beats intact. "
    "Return only the translated text with no extra commentary."
)


def translate_synopsis(synopsis: str, characters: list[str]) -> tuple[str, dict]:
    """
    Translate a synopsis to English, preserving character names as-is.
    Returns (translated_synopsis, usage_dict).
    """
    if not synopsis or not synopsis.strip():
        return synopsis, {"input_tokens": 0, "output_tokens": 0}

    agent = BaseAgent(system_prompt=_SYSTEM, temp=0.3)
    char_list = ", ".join(f'"{c}"' for c in characters) if characters else "none"
    query = (
        f"Character names to preserve as-is: [{char_list}]\n\n"
        f"Synopsis to translate:\n{synopsis}"
    )
    translated = agent(query, parse=False)
    return translated.strip(), agent.get_usage()
