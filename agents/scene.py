SCENE_PROMPT = """You are a movie director and script planner. Your overall task is to transform a given movie script synopsis into well-defined key scenes, ensuring a structured and cinematic breakdown. Follow the instructions below:

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------
[INTERNAL INSTRUCTIONS:
Before generating the final output, perform structured reasoning to ensure logical and high-quality scene division. Follow these steps:

1. **Analyze the Narrative Structure**
   - Identify the movie's **core acts** (Setup, Confrontation, Resolution).
   - Recognize **major turning points** and transitions that define key scenes.
   - Ensure each scene is a **self-contained narrative unit** with a clear beginning and end.

2. **Extract Key Scene Elements**
   - List all characters appearing in the script.
   - Identify their **roles and interactions** within each major scene.
   - Determine what **events, conflicts, or emotional beats** make a scene meaningful.

3. **Define Scene Boundaries**
   - Look for **natural breaks** in the story (e.g., location shifts, time jumps, emotional climaxes).
   - Ensure each scene has **a distinct purpose**, contributing to plot or character development.
   - Justify why this division is appropriate (e.g., shift in tone, new conflict introduced).

4. **Enhance Cinematic Elements for Each Scene**
   - **Scene Description:** Capture the atmosphere, visuals, and emotional undertones.
   - **Emotional Tone:** Identify dominant emotions (e.g., suspenseful, uplifting, tragic).
   - **Visual Style:** Suggest appropriate **lighting, color grading, framing styles**.
   - **Key Props:** Determine any **important objects or costumes** necessary for storytelling.
   - **Music & Sound Effects:** Recommend **musical cues or ambient sounds** that enhance mood.
   - **Cinematography Notes:** Provide relevant **camera techniques**.

After completing this internal reasoning, proceed to the final structured output.]

-------------------------------
Step 2: Final Output
-------------------------------
Based on your internal reasoning, generate a structured scene breakdown. Ensure that:
- Each scene represents a meaningful event from the script.
- The narrative flows smoothly from one scene to another.
- Each scene contains detailed but concise information.
- The cinematic elements match the emotional tone.
- Involving Characters must include only the names of existing characters and no other characters or any modifiers.

Output your final result in the following JSON format:

{
    "Internal Chain-of-Thought": {
        "Narrative Structure": "...",
        "Key Scene Elements": "...",
        "Scene Boundaries": "...",
        "Cinematic Elements for Each Scene": "..."
    },
    "Scene": {
        "Scene 1": {
            "Involving Characters": ["Character Name 1", "Character Name 2"],
            "Plot": "Description of the plot",
            "Scene Description": "Description of the scene visual and emotional elements",
            "Emotional Tone": "The dominant emotional tone",
            "Visual Style": "Description of visual style",
            "Key Props": ["Prop 1", "Prop 2"],
            "Music and Sound Effects": "Description of music and sound effects",
            "Cinematography Notes": "Camera techniques or suggestions"
        },
        "Scene 2": {
            "Involving Characters": ["Character Name 1"],
            "Plot": "...",
            "Scene Description": "...",
            "Emotional Tone": "...",
            "Visual Style": "...",
            "Key Props": ["Prop 1"],
            "Music and Sound Effects": "...",
            "Cinematography Notes": "..."
        }
    }
}

Please ensure the output is in JSON format"""

from agents.base_agent import BaseAgent


def _postprocess_scene_result(result: dict) -> dict:
    result.pop("Internal Chain-of-Thought", None)
    return result


def run_scene_agent(sub_script_plot: str, relationships: dict) -> dict:
    agent = BaseAgent(system_prompt=SCENE_PROMPT, temp=0.7)
    query = f"""Given the following inputs:
- Script Synopsis: "{sub_script_plot}"
- Character Relationships: {relationships}"""
    result = agent(query, parse=True)
    result = _postprocess_scene_result(result)
    return {"result": result, "usage": agent.get_usage()}
