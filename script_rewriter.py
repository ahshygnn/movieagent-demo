import time
from openai import OpenAI
import config

_SYSTEM_PROMPT = """你是一位专业的动画剧本改编师,擅长把零散、扁平的故事素材重组为适合分镜规划的叙事文本。

你的任务:将用户提供的原始故事输入,改写为一段叙事结构丰富的连续段落,使其更适合后续的导演分镜规划。

改写时需在以下三个维度上增强:
1. 叙事弧度:把并列的、孤立的动作描述,重组为有因果关系和情绪起伏的连贯叙事(例如:日常铺垫 → 异常出现 → 冲突/危机 → 决定 → 悬念收尾)。
2. 角色塑造:为出场角色补充简洁的性格或身份标签,使不同角色具有辨识度。
3. 情绪与场景:适度补充环境描写和情绪渲染,增强画面感和感官细节。

必须遵守的约束:
- 不得添加原文中不存在的核心情节、人物或结局。你只能重组和丰富"表达方式",不能改变"故事本身"。
- 保留原文的所有关键情节点和出场角色。
- 只输出改写后的段落正文。不要输出任何前言、解释、标题或"以下是改写版"之类的话。
- 不要分点、不要列提纲,输出为一段连贯的叙事散文。

参考示例(理解改写的方向和程度):

【原始输入】
小猫在灯塔里。它看见远处有船。船好像要撞上礁石。小猫跑去按灯塔的开关。灯亮了。船看见了灯,转了方向。船安全了。小猫睡着了。

【改写输出】
阿灯是一只独自守护海边灯塔的小猫,每个夜晚都尽职地巡视着它的塔楼。这天深夜,浓雾弥漫,阿灯透过窗户望见远处海面上一艘货船正缓缓驶来,而它的航线尽头,赫然是一片狰狞的礁石。阿灯的心一下子提了起来——再这样下去,船必然撞礁。它顾不上多想,飞快地冲向灯塔顶端那个沉重的开关,用尽全身力气扑了上去。刹那间,一束明亮的光柱划破浓雾,直直地射向海面。货船终于察觉到了危险,缓缓调转船头,绕开了那片致命的礁石,平稳地驶向远方。望着货船安全离去的背影,阿灯长长舒了一口气,蜷起身子,在灯塔温暖的光晕里安心地睡着了。"""

_USER_PROMPT_TEMPLATE = """现在,请改写以下输入,只输出改写后的段落正文:

【原始输入】
{raw_input}

【改写输出】"""


def rewrite_script(raw_input: str) -> dict:
    """
    把零散故事文本改写为叙事丰富的段落。

    Returns:
        rewritten: 改写后的正文
        input_tokens: prompt token 数
        output_tokens: completion token 数
        elapsed_seconds: 总耗时
    """
    if not raw_input or not raw_input.strip():
        raise ValueError("raw_input 不能为空")

    client = OpenAI(
        api_key=config.YIZHAN_API_KEY,
        base_url=config.YIZHAN_BASE_URL.rstrip("/"),
    )

    user_message = _USER_PROMPT_TEMPLATE.format(raw_input=raw_input.strip())
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    t0 = time.time()
    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=4096,
            timeout=120,
        )
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败: {e}") from e

    elapsed = round(time.time() - t0, 2)
    rewritten = response.choices[0].message.content.strip()

    if not rewritten:
        raise RuntimeError("LLM 返回了空内容")

    return {
        "rewritten": rewritten,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "elapsed_seconds": elapsed,
    }


if __name__ == "__main__":
    sample = """月光邮局的夜晚很安静,团团小心地打开一封会发光的信。
信纸上只有一句话:星星花园正在慢慢熄灭。
圆圆低头一看,一颗蓝色的星光碎片从信封里掉了出来。
蓝色碎片在桌上闪烁,像是在催促大家赶快出发。
团团和圆圆抬头望向彼此,心里都明白这不是一封普通的信。
墨墨翻开旧地图,寻找星光碎片的来处。
地图上的云朵山亮了起来,答案终于出现了。
三个伙伴围在地图旁,决定去云朵山寻找真相。
邮局外的天空中,一颗星星突然暗了下来,冒险也正式开始。"""

    print("正在改写...\n")
    result = rewrite_script(sample)
    print("【改写结果】")
    print(result["rewritten"])
    print(f"\n耗时: {result['elapsed_seconds']}s  |  input_tokens: {result['input_tokens']}  |  output_tokens: {result['output_tokens']}")
