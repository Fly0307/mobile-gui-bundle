"""
Standalone ReAct response parser for GELab-Zero action space.
No dependencies on the main project.
"""
import re
from collections import OrderedDict

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个手机 GUI-Agent 操作专家，你需要根据用户下发的任务、手机屏幕截图和交互操作的历史记录，借助既定的动作空间与手机进行交互，从而完成用户的任务。
请牢记，手机屏幕坐标系以左上角为原点，x轴向右，y轴向下，取值范围均为 0-1000。

# 行动原则：

1. 你需要明确记录自己上一次的action，如果是滑动，不能超过5次。
2. 你需要严格遵循用户的指令，如果你和用户进行过对话，需要更遵守最后一轮的指令

# Action Space:

在 Android 手机的场景下，你的动作空间包含以下9类操作，所有输出都必须遵守对应的参数要求：
1. CLICK：点击手机屏幕坐标，需包含点击的坐标位置 point。
例如：action:CLICK\tpoint:x,y
2. TYPE：在手机输入框中输入文字，需包含输入内容 value、输入框的位置 point。
例如：action:TYPE\tvalue:输入内容\tpoint:x,y
3. COMPLETE：任务完成后向用户报告结果，需包含报告的内容 value。
例如：action:COMPLETE\treturn:完成任务后向用户报告的内容
4. WAIT：等待指定时长，需包含等待时间 value（秒）。
例如：action:WAIT\tvalue:等待时间
5. AWAKE：唤醒指定应用，需包含唤醒的应用名称 value。
例如：action:AWAKE\tvalue:应用名称
6. INFO：询问用户问题或详细信息，需包含提问内容 value。
例如：action:INFO\tvalue:提问内容
7. ABORT：终止当前任务，仅在当前任务无法继续执行时使用，需包含 value 说明原因。
例如：action:ABORT\tvalue:终止任务的原因
8. SLIDE：在手机屏幕上滑动，滑动的方向不限，需包含起点 point1 和终点 point2。
例如：action:SLIDE\tpoint1:x1,y1\tpoint2:x2,y2
9. LONGPRESS：长按手机屏幕坐标，需包含长按的坐标位置 point。
例如：action:LONGPRESS\tpoint:x,y
"""

VALID_ACTIONS = {"CLICK", "TYPE", "COMPLETE", "WAIT", "AWAKE", "INFO", "ABORT", "SLIDE", "LONGPRESS"}


# ── App detection (pre-loop) ──────────────────────────────────────────────────

APP_DETECTION_PROMPT = """你是一个手机操作助手。用户会给你一个任务描述。
你需要根据任务描述判断需要首先打开哪个APP才能完成这个任务。

请分析任务内容，确定需要打开的第一个APP，并输出 AWAKE 动作。

输出格式（只需输出这一行，不要输出其他内容）：
action:AWAKE\tvalue:APP名称

示例：
action:AWAKE\tvalue:微信
action:AWAKE\tvalue:淘宝
action:AWAKE\tvalue:设置

如果任务不需要打开任何特定APP（例如"查看通知栏"、"截图"等不涉及特定APP的系统操作），请输出：
action:AWAKE\tvalue:
"""


def build_app_detection_messages(
    task: str,
    screenshot_data_url: str,
) -> list:
    """Build messages for the pre-loop app detection step."""
    content = [
        {"type": "text", "text": APP_DETECTION_PROMPT},
        {
            "type": "text",
            "text": f"\n用户任务：{task}\n\\n",
        },
        {
            "type": "text",
            "text": "\n请判断需要打开哪个APP，输出 AWAKE 动作："},
    ]
    return [{"role": "user", "content": content}]


# ── Message builder ────────────────────────────────────────────────────────────

def build_messages(
    task: str,
    screenshot_data_url: str,
    summary_history: str = "",
    qa_pairs: list = None,
    think: bool = True,
) -> list:
    """
    Build the OpenAI-format messages list for one agent step.

    Args:
        task: Natural language task description.
        screenshot_data_url: Current screenshot as a data URL (data:image/...).
        summary_history: Cumulative action summary from the previous step.
        qa_pairs: List of (question, answer) tuples from previous INFO interactions.
    """
    if qa_pairs is None:
        qa_pairs = []

    history_display = summary_history.strip() if summary_history.strip() else "暂无历史操作"

    qa_prompt = ""
    if qa_pairs:
        qa_lines = "\n".join(
            f"你曾经提出的问题：{q}\n\n用户对你的指示：{a}" for q, a in qa_pairs
        )
        qa_prompt = f"这是你和用户的对话历史： \n{qa_lines}\n\n 你需要更加注意用户最后的指示。 "

    user_instruction = f"\n\n{qa_prompt}\n\n指令结束\n\n" if qa_prompt else "指令结束\n\n"
    full_task = task + user_instruction

    content = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {
            "type": "text",
            "text": (
                f"\n\n已知用户指令为：{full_task}"
                f"已知已经执行过的历史动作如下：{history_display}"
                "\n当前手机屏幕截图如下：\n"
            ),
        },
        {"type": "image_url", "image_url": {"url": screenshot_data_url}},
        {
            "type": "text",
            "text": (
                (
                    "\n\n在执行操作之前，请务必回顾你的历史操作记录和限定的动作空间，"
                    "先进行思考和解释然后输出动作空间和对应的参数：\n"
                    "1. 思考（THINK）：在 <THINK> 和 </THINK> 标签之间。\n"
                    "2. 解释（explain）：在动作格式中，使用 explain: 开头，简要说明当前动作的目的和执行方式。\n"
                    "在执行完操作后，请输出执行完当前步骤后的新历史总结。\n"
                    "输出格式示例：\n"
                    "<THINK> 思考的内容 </THINK>\n"
                    "explain:解释的内容\taction:动作空间和对应的参数\tsummary:执行完当前步骤后的新历史总结\n"
                ) if think else (
                    "\n\n在执行操作之前，请务必回顾你的历史操作记录和限定的动作空间，"
                    "直接输出动作空间和对应的参数，不要输出任何 <THINK> 思考块：\n"
                    "解释（explain）：在动作格式中，使用 explain: 开头，简要说明当前动作的目的和执行方式。\n"
                    "在执行完操作后，请输出执行完当前步骤后的新历史总结。\n"
                    "注意：禁止输出 <THINK>...</THINK> 内容，直接从 explain: 开始输出。\n"
                    "输出格式示例：\n"
                    "explain:解释的内容\taction:动作空间和对应的参数\tsummary:执行完当前步骤后的新历史总结\n"
                )
            ),
        },
    ]

    return [{"role": "user", "content": content}]


# ── Response parser ────────────────────────────────────────────────────────────

def str2action(response_text: str, think: bool = True) -> dict:
    """
    Parse a ReAct-style LLM response into a structured action dict.

    Returns an OrderedDict with at minimum: cot, action.
    Raises ValueError on unrecognised action types or missing required fields.
    """
    text = response_text.strip()

    if think:
        # Normalise THINK tag variants
        text = (
            text
            .replace("<TINK>", "<THINK>").replace("</TINK>", "</THINK>")
            .replace("<think>", "<THINK>").replace("</think>", "</THINK>")
        )
        text = re.sub(
            r"<\s*/?THINK\s*>",
            lambda m: "<THINK>" if "/" not in m.group() else "</THINK>",
            text,
            flags=re.IGNORECASE,
        )

        # Extract CoT and KV sections
        try:
            cot = text.split("<THINK>")[1].split("</THINK>")[0].strip()
            kv_part = text.split("</THINK>")[1].strip()
        except IndexError:
            print("[Parser] Missing <THINK> tags, treating full response as KV")
            cot = ""
            kv_part = text
    else:
        # Strip any THINK block the model may have output despite not being asked
        text = re.sub(r"<THINK>.*?</THINK>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
        cot = ""
        kv_part = text

    action = OrderedDict()
    action["cot"] = cot

    for kv in (kv.strip() for kv in kv_part.split("\t") if kv.strip()):
        if ":" not in kv:
            continue
        key, value = kv.split(":", 1)
        key = key.strip()
        value = value.strip()

        if "point" in key:
            try:
                coords = value.replace(",", " ").split()
                action[key] = [int(coords[0]), int(coords[1])]
            except (ValueError, IndexError) as e:
                raise ValueError(f"Cannot parse point '{value}' for key '{key}'") from e
        else:
            action[key] = value

    if "action" not in action:
        raise ValueError(f"No 'action' field found in LLM response:\n{response_text}")

    action_type = action["action"].upper()
    if action_type not in VALID_ACTIONS:
        raise ValueError(f"Unknown action type '{action_type}'")

    action["action"] = action_type
    return action
