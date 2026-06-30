from openai import OpenAI
import json
import time
import config


def _strip_json_fence(content: str) -> str:
    return content.replace("```json", "").replace("```", "").strip()


def _json_object_slice(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        return content[start:end + 1]
    return content


class BaseAgent:
    def __init__(self, system_prompt="", temp=0.7):
        self.client = OpenAI(
            api_key=config.YIZHAN_API_KEY,
            base_url=config.YIZHAN_BASE_URL.rstrip("/")
        )
        self.system = system_prompt
        self.temp = temp
        self.input_tokens = 0
        self.output_tokens = 0
        self.parse_attempts = 0
        self.parse_successes = 0

    def __call__(self, message: str, parse=True, max_retries=3):
        user_message = message
        if parse:
            user_message = (
                f"{message}\n\n"
                "Return only one valid JSON object. Do not include markdown, comments, or explanatory text."
            )
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user_message}
        ]

        last_error = None
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": config.LLM_MODEL,
                    "messages": messages,
                    "temperature": self.temp,
                    "max_tokens": 4096,
                    "timeout": 120,
                }
                response = self.client.chat.completions.create(**kwargs)

                self.input_tokens += response.usage.prompt_tokens
                self.output_tokens += response.usage.completion_tokens
                content = response.choices[0].message.content

                if parse:
                    content = _json_object_slice(_strip_json_fence(content))
                    self.parse_attempts += 1
                    try:
                        parsed = json.loads(content)
                        self.parse_successes += 1
                        return parsed
                    except json.JSONDecodeError as e:
                        try:
                            repaired = self._repair_json(content, str(e))
                            parsed = json.loads(repaired)
                            self.parse_successes += 1
                            return parsed
                        except Exception as repair_error:
                            last_error = repair_error
                            print(f"[重试 {attempt+1}/{max_retries}] JSON解析失败：{e}")
                            continue

                return content

            except Exception as e:
                last_error = e
                wait = (attempt + 1) * 10
                print(f"[重试 {attempt+1}/{max_retries}] 调用失败：{e}，等待{wait}秒...")
                time.sleep(wait)
                continue

        raise Exception(f"已重试{max_retries}次仍失败。最后错误：{last_error}")

    def get_usage(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "parse_attempts": self.parse_attempts,
            "parse_successes": self.parse_successes,
        }

    def _repair_json(self, invalid_json: str, error: str) -> str:
        response = self.client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You repair malformed JSON. Return exactly one valid JSON object. "
                        "Do not add markdown or explanations. Preserve all keys and values."
                    ),
                },
                {
                    "role": "user",
                    "content": f"JSON parse error: {error}\n\nMalformed JSON:\n{invalid_json}",
                },
            ],
            temperature=0,
            max_tokens=4096,
            timeout=120,
            response_format={"type": "json_object"},
        )
        self.input_tokens += response.usage.prompt_tokens
        self.output_tokens += response.usage.completion_tokens
        return _json_object_slice(_strip_json_fence(response.choices[0].message.content))
