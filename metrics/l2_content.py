"""
L2 Content Quality Metrics — LLM-as-judge (narrative coherence).
Uses the existing LLM API, no extra dependencies needed.
"""
import json
from openai import OpenAI
import config

_JUDGE_SYSTEM = """You are an expert film critic and narrative analyst.
Rate the narrative coherence of the shot description against the original script.

Scoring criteria (1-5):
5 = Perfectly captures the scene; characters, actions and emotion match the script exactly
4 = Mostly coherent; minor omissions or rewording, core narrative intact
3 = Partially coherent; some key plot points missing or mildly altered
2 = Mostly incoherent; significant departures from the original script
1 = Unrelated or contradicts the original script

Return ONLY a valid JSON object with no extra text:
{"score": <integer 1-5>, "reason": "<one concise sentence>"}"""


def score_shot(original_script: str, shot_plot: str) -> dict:
    client = OpenAI(
        api_key=config.YIZHAN_API_KEY,
        base_url=config.YIZHAN_BASE_URL.rstrip("/"),
    )
    try:
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Original Script:\n{original_script}\n\n"
                        f"Shot Description:\n{shot_plot}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return {
            "score": int(data.get("score", 0)),
            "reason": data.get("reason", ""),
            "error": None,
        }
    except Exception as e:
        return {"score": None, "reason": None, "error": str(e)}


def run_narrative_scoring(task_id: str, original_script: str, shots: dict) -> dict:
    """
    Score all shots and persist results.

    shots: tasks[task_id]["shots"]
      = {ss_name: {scene_name: {"Shot": {shot_name: shot_data}}}}
    """
    from metrics.collector import load_metrics, record_narrative_scores

    m = load_metrics(task_id)
    m["l2"]["narrative_coherence"]["status"] = "running"
    from metrics.collector import save_metrics
    save_metrics(task_id, m)

    scores: dict[str, dict] = {}
    for ss_name, scenes in shots.items():
        for scene_name, scene_data in scenes.items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                key = f"{ss_name}__{scene_name}__{shot_name}"
                plot = shot_data.get("Plot/Visual Description", "")
                scores[key] = score_shot(original_script, plot)

    record_narrative_scores(task_id, scores)
    return scores
