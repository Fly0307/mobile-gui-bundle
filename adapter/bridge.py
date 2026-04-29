#!/usr/bin/env python3
"""
Mobile GUI Plugin Bridge — called by OpenClaw TS plugin via subprocess.
Self-contained: all dependencies are in adapter/ alongside this file.
Usage: python bridge.py <command> --args '<JSON>'
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from session_store import create_session, load_session, save_session  # noqa: E402
from pausable_agent import PausableAgentLoop                           # noqa: E402


_PLUGIN_DIR = os.path.dirname(_HERE)
_CONFIG_PATH = os.path.join(_PLUGIN_DIR, "config.yaml")
_EXAMPLE_PATH = os.path.join(_PLUGIN_DIR, "config.example.yaml")


def _check_config() -> dict | None:
    """Return needs_setup dict if config.yaml missing or api_base not set, else None."""
    if not os.path.exists(_CONFIG_PATH):
        return {
            "status": "needs_setup",
            "message": "插件尚未初始化，config.yaml 不存在。",
            "required_fields": [
                {"key": "llm.api_base",   "description": "LLM 服务地址，如 http://127.0.0.1:7003/v1"},
                {"key": "llm.api_key",    "description": "API 密钥，无鉴权填 EMPTY"},
                {"key": "llm.model_name", "description": "模型名称，如 MobiMind-1.5-4B"},
            ],
            "optional_fields": [
                {"key": "adb.device",     "description": "ADB 设备地址，留空自动检测"},
                {"key": "llm.image_resize", "description": "截图压缩尺寸，建议 [728, 728]"},
            ],
            "next_step": "请调用 setup 命令并提供上述字段值完成初始化",
        }
    return None


def _load_config(config_path: str | None = None) -> dict:
    import yaml
    if not config_path:
        config_path = _CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _bridge_url(config: dict) -> str:
    h = config.get("bridge", {}).get("host", "127.0.0.1")
    p = config.get("bridge", {}).get("port", 8765)
    return f"http://{h}:{p}"


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_device_status(args: dict) -> dict:
    if err := _check_config():
        return err
    result: dict = {"adb_connected": False, "plugin_available": False}
    try:
        config = _load_config(args.get("config_path"))
        device = config.get("adb", {}).get("device", "")
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        if device:
            result["adb_connected"] = any(
                line.startswith(device) and "device" in line
                for line in r.stdout.splitlines()
            )
        else:
            result["adb_connected"] = "device" in r.stdout
        result["plugin_available"] = True
        result["device"] = device
        result["bridge_url"] = _bridge_url(config)
        result["start_bridge_cmd"] = f"bash {os.path.join(_PLUGIN_DIR, 'scripts', 'start_bridge.sh')} &"
    except Exception as e:
        result["error"] = str(e)
    return result


def cmd_observe(args: dict) -> dict:
    if err := _check_config():
        return err
    try:
        import base64
        import requests
        config = _load_config(args.get("config_path"))
        resp = requests.get(f"{_bridge_url(config)}/screenshot", timeout=15)
        resp.raise_for_status()
        data = resp.json()

        sc_dir = os.path.expanduser("~/.openclaw/mobile_gui/screenshots")
        os.makedirs(sc_dir, exist_ok=True)
        sc_path = os.path.join(sc_dir, f"obs_{uuid.uuid4().hex[:6]}.png")
        with open(sc_path, "wb") as f:
            f.write(base64.b64decode(data["image"]))

        return {"status": "ok", "screenshot_path": sc_path,
                "width": data["width"], "height": data["height"]}
    except Exception as e:
        msg = str(e)
        if "Connection refused" in msg or "NewConnectionError" in msg:
            msg = "adb_bridge 未启动，请先运行 bundle 内的 scripts/start_bridge.sh。\n" + msg
        return {"status": "error", "error": msg}


def cmd_start_task(args: dict) -> dict:
    if err := _check_config():
        return err
    goal = args.get("goal", "")
    if not goal:
        return {"status": "failed", "error": "goal is required"}
    try:
        config = _load_config(args.get("config_path"))
        if "max_steps" in args:
            config.setdefault("agent", {})["max_steps"] = int(args["max_steps"])

        session = create_session(goal, config)
        loop = PausableAgentLoop(config, bridge_url=_bridge_url(config))
        result = loop.run_pausable(goal, session, debug=args.get("debug", False))
        _update_session(session, result)
        result["task_id"] = session["task_id"]
        return result
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def cmd_resume_task(args: dict) -> dict:
    task_id = args.get("task_id", "")
    resume_token = args.get("resume_token", "")
    user_response = args.get("user_response", "")

    session = load_session(task_id)
    if not session:
        return {"status": "failed", "error": f"Session {task_id} not found"}
    if session.get("resume_token") != resume_token:
        return {"status": "failed", "error": "Invalid resume_token"}
    if session.get("status") != "paused":
        return {"status": "failed", "error": f"Session not paused (status={session.get('status')})"}

    try:
        qa_pairs = session.get("qa_pairs", [])
        qa_pairs.append([session.get("pending_question", ""), user_response])
        session.update({"qa_pairs": qa_pairs, "pending_question": None,
                        "resume_token": None, "status": "running"})
        save_session(session)

        config = session["config"]
        loop = PausableAgentLoop(config, bridge_url=_bridge_url(config))
        result = loop.run_pausable(session["goal"], session, debug=args.get("debug", True))
        _update_session(session, result)
        result["task_id"] = task_id
        return result
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def cmd_cancel_task(args: dict) -> dict:
    task_id = args.get("task_id", "")
    session = load_session(task_id)
    if not session:
        return {"status": "failed", "error": f"Session {task_id} not found"}
    session["status"] = "cancelled"
    save_session(session)
    return {"status": "cancelled", "task_id": task_id}


def _update_session(session: dict, result: dict) -> None:
    if result["status"] == "needs_user_input":
        cp = result["checkpoint"]
        session.update({"status": "paused", "step_index": cp["step_index"],
                        "summary_history": cp["summary_history"],
                        "qa_pairs": cp["qa_pairs"],
                        "pending_question": cp["pending_question"],
                        "resume_token": cp["resume_token"]})
    else:
        session["status"] = result["status"]
    save_session(session)


def cmd_setup(args: dict) -> dict:
    """Create or update config.yaml with provided field values.

    args keys match dot-notation field paths, e.g.:
      {"llm.api_base": "http://...", "llm.model_name": "xxx"}
    """
    import yaml

    # Load example as base if config doesn't exist yet
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    elif os.path.exists(_EXAMPLE_PATH):
        with open(_EXAMPLE_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    # Apply provided fields (dot-notation keys)
    updated = []
    for key, value in args.items():
        if key in ("config_path", "debug"):
            continue
        parts = key.split(".")
        node = config
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        updated.append(key)

    if not updated:
        return {
            "status": "failed",
            "error": "未提供任何配置字段。",
            "required_fields": [
                {"key": "llm.api_base",   "description": "LLM 服务地址"},
                {"key": "llm.api_key",    "description": "API 密钥，无鉴权填 EMPTY"},
                {"key": "llm.model_name", "description": "模型名称"},
            ],
        }

    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Check required fields are now present
    missing = []
    for req in ["llm.api_base", "llm.model_name"]:
        parts = req.split(".")
        node = config
        for part in parts:
            node = node.get(part) if isinstance(node, dict) else None
        if not node:
            missing.append(req)

    result = {"status": "ok", "updated_fields": updated, "config_path": _CONFIG_PATH}
    if missing:
        result["status"] = "incomplete"
        result["missing_required"] = missing
        result["message"] = f"配置已保存，但以下必填字段仍缺失：{missing}"
    else:
        result["message"] = "配置初始化完成，可以开始使用插件。"
    return result


COMMANDS = {
    "device_status": cmd_device_status,
    "observe": cmd_observe,
    "start_task": cmd_start_task,
    "resume_task": cmd_resume_task,
    "cancel_task": cmd_cancel_task,
    "setup": cmd_setup,
}


def main():
    t0 = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=list(COMMANDS.keys()))
    parser.add_argument("--args", default="{}")
    parser.add_argument("--debug", action="store_true", help="Print LLM responses and actions to stderr")
    parsed = parser.parse_args()
    try:
        call_args = json.loads(parsed.args)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "failed", "error": f"Invalid JSON: {e}"}))
        sys.exit(1)
    if parsed.debug:
        call_args["debug"] = True

    cmd_t0 = time.time()
    result = COMMANDS[parsed.command](call_args)
    cmd_elapsed = time.time() - cmd_t0
    total_elapsed = time.time() - t0
    print(f"[Timing] bridge command={parsed.command} command_cost={cmd_elapsed:.3f}s total={total_elapsed:.3f}s", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
