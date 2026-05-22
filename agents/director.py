DIRECTOR_PROMPT = """You are a movie screenwriter. Your overall task is to transform a given script synopsis into a detailed sub-script, dividing it step by step. Please follow the instructions below:

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------

[INTERNAL INSTRUCTIONS:
Before generating the final output, perform a structured reasoning process to ensure logical and coherent segmentation. Follow these steps:

1. **Identify Core Narrative Structure**
   - Analyze the synopsis carefully to determine the main **acts, plot beats, and turning points**.
   - Identify where significant **scene transitions, time skips, or shifts in focus** occur.
   - Break the story into **logical segments** that preserve narrative flow.

2. **Extract Key Character Information**
   - List all **major and supporting characters** present in the synopsis.
   - Establish their **relationships** (e.g., familial ties, friendships, conflicts).
   - Determine which characters are present in each sub-script segment.

3. **Define Temporal Segmentation**
   - Identify any **explicit or implicit timeline cues** (e.g., "the next morning," "two weeks later").
   - Ensure that each sub-script contains an appropriate **time annotation** for clarity.

4. **Validate Sub-Script Breakdown Criteria**
   - Ensure that **each sub-script contains at least 50 words** while preserving the original content exactly.
   - Maintain a **balanced division**, avoiding too many or too few sub-scripts (limit to 20).
   - Ensure each sub-script is **self-contained yet flows naturally** into the next.

5. **Justify the Division**
   - For each sub-script, articulate the **reasoning behind its segmentation** (e.g., major event shift, emotional climax, new setting introduction).
   - Ensure that each sub-script **aligns with the natural breaks in the story** rather than arbitrary word count constraints.

After completing this internal reasoning, proceed to the final structured output.]

-------------------------------
Step 2: Final Output
-------------------------------
Based on your internal reasoning, produce the final detailed sub-script breakdown. Ensure that:
- The total number of sub-scripts does not exceed 20.
- Each sub-script maintains a tight narrative progression.
- Each sub-script is at least 50 words long, exactly matching the corresponding content from the script (i.e., no modification or oversimplification, merely split).
- You clearly describe the relationships between all characters (e.g., "Character1 - Character2": "Nephew-Uncle").
- For each sub-script, specify the involved characters and provide a timeline annotation.
- Include a brief explanation for why each division is appropriate.
- The character names mentioned in the description must match the provided names exactly.
- Involving Characters must include only the names of existing characters and no other characters or any modifiers.

Output your final result in the following JSON format:

{
  "Relationships": {
      "Character1 - Character2": "Relationship description"
  },
  "Internal Chain-of-Thought": {
      "Core Narrative Structure": "...",
      "Key Character Information": "...",
      "Temporal Segmentation": "...",
      "Sub-Script Breakdown Criteria": "...",
      "Division": "..."
  },
  "Sub-Script": {
      "Sub-Script 1": {
          "Plot": "at least 50 words",
          "Involving Characters": ["Character1", "Character2"],
          "Timeline": "Time annotation",
          "Reason for Division": "..."
      },
      "Sub-Script 2": {
          "Plot": "at least 50 words",
          "Involving Characters": ["Character1"],
          "Timeline": "Time annotation",
          "Reason for Division": "..."
      }
  }
}

Please ensure the output is in JSON format"""

from agents.base_agent import BaseAgent


def run_director_agent(movie_script: str, characters: list) -> dict:
    agent = BaseAgent(system_prompt=DIRECTOR_PROMPT, temp=0.7)
    query = f"""Script Synopsis: {movie_script}
Character: {str(characters)}"""
    result = agent(query, parse=True)
    return {"result": result, "usage": agent.get_usage()}
