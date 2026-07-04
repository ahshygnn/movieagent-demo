SHOT_PROMPT = """You are a professional movie director. Your task is to transform the provided scene details into a well-structured shot list that effectively captures the emotions, plot, and visual storytelling. Follow the structured reasoning process below before generating the final output.

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------
[INTERNAL INSTRUCTIONS:
Before generating the final output, perform structured reasoning to ensure logical and high-quality shot composition. Follow these steps:

1. **Break Down Scene into Key Shots**
   - Identify the **essential moments** in the scene that require distinct shots.
   - Ensure that each shot serves a **clear narrative or emotional purpose**.
   - Determine logical transitions between shots to maintain visual continuity.

2. **Define Shot Composition and Framing**
   - Select the appropriate **shot type** (e.g., close-up for emotion, wide shot for setting).
   - Ensure framing adheres to **cinematic principles** (e.g., rule of thirds, leading lines).
   - Identify the **key objects and characters** that must be visible in the frame.

3. **Determine Character Positioning & Bounding Boxes**
   - Place characters using **normalized bounding boxes**, ensuring proper distribution in the frame.
   - Ensure that bounding boxes **do not exceed an interpolation of 0.5**.
   - Make the bounding boxes **as large as possible** to focus on key characters.
   - Bounding boxes must not intersect or overlap.

4. **Enhance Emotional Impact**
   - Identify the **dominant emotion** for each shot (e.g., fear, sadness, triumph).
   - Adjust lighting, depth of field, and contrast to reinforce the emotional tone.
   - Ensure continuity in background descriptions to maintain visual coherence.

5. **Refine Camera Techniques and Movements**
   - Specify **camera movements** (e.g., static shot for tension, dolly-in for intimacy).
   - Adjust angles dynamically to maintain narrative engagement.

6. **Ensure Physical Plausibility** (apply to all motion descriptions in "Coarse Plot" and "Camera Movement"):
   - **Clear Direction & Orientation (CRITICAL — prevent backward movement):** Always explicitly state the character's FACING direction and MOVEMENT direction, and ensure they are consistent (a character must face the direction they move toward). When a character walks or runs, describe them moving FORWARD in the direction they face (e.g., "facing forward, walking ahead" / "facing right, walking to the right"). NEVER describe motion that could be read as moving backward, unless walking backward is explicitly intended. Give a clear spatial path (e.g., "from background toward foreground" / "from left to right").
   - **Gravity & Trajectory:** Describe motion with proper gravity. For jumping, leaping, or falling, include the natural arc (e.g., "pushes off, arcs upward, then lands into...") rather than flat, floating movement.
   - **Natural Speed & Inertia:** Describe motion with natural acceleration and deceleration and smooth transitions. Avoid abrupt start-stop; include the build-up and follow-through of an action.
   - **Weight & Material Realism:** Reflect the weight and material of objects (heavy objects move with visible effort and momentum; cloth and hair sway softly and naturally).
   - **Avoid Action Pile-up:** Within a ~5-second shot, describe motion as a natural sequence over time rather than compressing multiple actions into a single instant.
   - These guidelines apply to motion descriptions only; do NOT add any explanatory physics text to the JSON output.

7. **Ensure Dialogue Accuracy**
   - Extract **relevant dialogue** for each shot, ensuring proper pacing.
   - Format dialogue in JSON structure with character names.
   - All dialogue in `Dialogue` must be written in Simplified Chinese.
   - If the source story or scene is in English, translate or localize the dialogue into natural Simplified Chinese.

After completing this internal reasoning, proceed to the final structured output.]

-------------------------------
Step 2: Final Output
-------------------------------
Based on your internal reasoning, generate a structured shot list. Ensure that:
- Each shot contributes to narrative flow and emotional impact.
- Each Scene should contain about 2-4 shots (prefer 2-4). Prioritize covering the scene's key actions and emotional beats completely; only merge shots when they are clearly redundant or repetitive. Do not pad a scene with unnecessary shots, but do not omit shots needed to tell the scene clearly.
- All motion in "Coarse Plot" and "Camera Movement" must follow the Physical Plausibility principles: explicit forward-consistent direction, correct gravity/trajectory, natural speed/inertia, realistic weight, and no action pile-up.
- Character positioning follows bounding box constraints [x, y, x1, y1] (normalized, interpolation must not exceed 0.5).
- Bounding boxes must not intersect or overlap.
- Dialogue is formatted properly in JSON.
- Dialogue in `Dialogue` must be Simplified Chinese, even when the input script is English.
- The character names mentioned in the description must match the provided names exactly.
- Each shot should include no more than three characters, preferably one or two. This ensures alignment with the image generation model's maximum of three character reference images.
- Involving Characters must include only the names of existing characters and no modifiers.

Output your final result in the following JSON format:

{
    "Internal Chain-of-Thought": {
        "Break Down Scene into Key Shots": "...",
        "Shot Composition and Framing": "...",
        "Character Positioning & Bounding Boxes": "...",
        "Emotional Impact": "...",
        "Camera Techniques and Movements": "...",
        "Dialogue Accuracy": "..."
    },
    "Shot": {
        "Shot 1": {
            "Involving Characters": {
                "Character 1": [0.1, 0.06, 0.49, 1.0],
                "Character 2": [0.58, 0.04, 0.95, 1.0]
            },
            "Plot/Visual Description": "Detailed description more than 30 words",
            "Coarse Plot": "No character names, actions only, less than 20 words",
            "Emotional Enhancement": "...",
            "Shot Type": "Type of shot",
            "Camera Movement": "Description of camera movement",
            "Dialogue": {
                "Character 1": "Dialogue content"
            }
        },
        "Shot 2": {
            "Involving Characters": {
                "Character 1": [0.1, 0.06, 0.49, 1.0]
            },
            "Plot/Visual Description": "...",
            "Coarse Plot": "...",
            "Emotional Enhancement": "...",
            "Shot Type": "...",
            "Camera Movement": "...",
            "Dialogue": {
                "Character 1": "..."
            }
        }
    }
}

Please ensure the output is in JSON format"""

from agents.base_agent import BaseAgent
import config


def _boxes_overlap(box_a: list, box_b: list) -> bool:
    if not (isinstance(box_a, list) and len(box_a) == 4 and isinstance(box_b, list) and len(box_b) == 4):
        return False
    x1a, y1a, x2a, y2a = box_a
    x1b, y1b, x2b, y2b = box_b
    return x1a < x2b and x2a > x1b and y1a < y2b and y2a > y1b


def _fix_bounding_boxes(shot_data: dict) -> None:
    involving = shot_data.get("Involving Characters")
    if not isinstance(involving, dict) or len(involving) < 2:
        return
    chars = list(involving.keys())
    boxes = [involving[c] for c in chars]
    has_overlap = any(
        _boxes_overlap(boxes[i], boxes[j])
        for i in range(len(boxes))
        for j in range(i + 1, len(boxes))
    )
    if not has_overlap:
        return
    # Redistribute evenly across horizontal axis
    n = len(chars)
    step = 1.0 / n
    for i, char in enumerate(chars):
        involving[char] = [round(i * step + 0.01, 2), 0.0, round((i + 1) * step - 0.01, 2), 1.0]


def _postprocess_shot_result(result: dict) -> dict:
    result.pop("Internal Chain-of-Thought", None)
    for shot_data in result.get("Shot", {}).values():
        _fix_bounding_boxes(shot_data)
    return result


def run_shot_agent(scene_details: dict) -> dict:
    agent = BaseAgent(system_prompt=SHOT_PROMPT, temp=0.7)
    shot_count_instruction = ""
    if config.SHOT_MAX_PER_SCENE > 0:
        shot_count_instruction = (
            f'\n- Generation mode: "{config.GENERATION_MODE}". '
            f"Create no more than {config.SHOT_MAX_PER_SCENE} essential shots for this scene. "
            "Prioritize story clarity and avoid transitional or redundant shots."
        )
    style_anchor = scene_details.get("Visual Style", "")
    query = f"""Given the following Scene Details:
- Involving Characters: "{scene_details['Involving Characters']}"
- Plot: "{scene_details['Plot']}"
- Scene Description: "{scene_details['Scene Description']}"
- Emotional Tone: "{scene_details['Emotional Tone']}"
- Key Props: {scene_details['Key Props']}
- Cinematography Notes: "{scene_details['Cinematography Notes']}"
- [STYLE ANCHOR] Visual Style (must remain strictly consistent across every shot in this scene): "{style_anchor}"
{shot_count_instruction}
"""
    result = agent(query, parse=True)
    result = _postprocess_shot_result(result)
    return {"result": result, "usage": agent.get_usage()}
