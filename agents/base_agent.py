from openai import OpenAI
import json
import time
import config


class BaseAgent:
    def __init__(self, system_prompt="", temp=0.7):
        self.client = OpenAI(
            api_key=config.YIZHAN_API_KEY,
            base_url=config.YIZHAN_BASE_URL + "/v1"
        )
        self.system = system_prompt
        self.temp = temp
        self.input_tokens = 0
        self.output_tokens = 0

    def __call__(self, message: str, parse=True, max_retries=3):
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": message}
        ]

        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=messages,
                    temperature=self.temp,
                    max_tokens=4096,
                    timeout=120
                )

                self.input_tokens += response.usage.prompt_tokens
                self.output_tokens += response.usage.completion_tokens
                content = response.choices[0].message.content

                if parse:
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError as e:
                        last_error = e
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
            "output_tokens": self.output_tokens
        }
