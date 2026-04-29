"""
Main agent loop: orchestrates screenshot → LLM → parse → execute.
Calls adb_bridge via HTTP and uses llm_client for inference.
"""
import base64
import json
import os
import time
import uuid
from datetime import datetime

import requests

try:
    from .action_parser import build_app_detection_messages, build_messages, str2action
    from .llm_client import ask_llm, image_to_data_url
except ImportError:
    from action_parser import build_app_detection_messages, build_messages, str2action  # type: ignore[no-redef]
    from llm_client import ask_llm, image_to_data_url  # type: ignore[no-redef]


class AgentLoop:
    def __init__(self, config: dict, bridge_url: str = "http://127.0.0.1:8765"):
        self.cfg = config
        self.bridge = bridge_url.rstrip("/")
        self.llm_cfg = config["llm"]
        self.agent_cfg = config.get("agent", {})

    # ── Bridge helpers ─────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict:
        t0 = time.time()
        timeout = self.cfg.get("bridge", {}).get("request_timeout", 30)
        resp = requests.get(f"{self.bridge}{path}", timeout=timeout)
        resp.raise_for_status()
        print(f"[Timing] GET {path}: {time.time()-t0:.3f}s")
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        t0 = time.time()
        timeout = self.cfg.get("bridge", {}).get("request_timeout", 30)
        resp = requests.post(f"{self.bridge}{path}", json=body, timeout=timeout)
        resp.raise_for_status()
        print(f"[Timing] POST {path}: {time.time()-t0:.3f}s")
        return resp.json()

    def _screenshot(self) -> tuple:
        """Returns (image_bytes, data_url). Resize is handled by ask_llm()."""
        t0 = time.time()
        data = self._get("/screenshot")
        t1 = time.time()
        image_bytes = base64.b64decode(data["image"])
        t2 = time.time()
        data_url = image_to_data_url(image_bytes)
        t3 = time.time()
        print(f"[Timing] screenshot total={t3-t0:.3f}s (http={t1-t0:.3f}s decode={t2-t1:.3f}s encode={t3-t2:.3f}s)")
        return image_bytes, data_url

    def _execute(self, action: dict, reflush_app: bool = True) -> None:
        action_type = action["action"]
        t0 = time.time()

        if action_type == "CLICK":
            pt = action["point"]
            self._post("/tap", {"x": pt[0], "y": pt[1]})

        elif action_type == "LONGPRESS":
            pt = action["point"]
            self._post("/longpress", {"x": pt[0], "y": pt[1]})

        elif action_type == "TYPE":
            body: dict = {"text": action.get("value", "")}
            if "point" in action:
                body["x"], body["y"] = action["point"][0], action["point"][1]
            self._post("/type", body)

        elif action_type == "SLIDE":
            p1, p2 = action["point1"], action["point2"]
            self._post("/swipe", {"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})

        elif action_type == "AWAKE":
            post_launch_sleep = self.agent_cfg.get("post_launch_sleep", 2.0)
            self._post("/launch", {"app_name": action.get("value", ""), "reflush": reflush_app})
            time.sleep(post_launch_sleep)

        elif action_type == "WAIT":
            wait_default = self.agent_cfg.get("wait_action_default", 2.0)
            try:
                secs = float(action.get("value", wait_default))
            except (ValueError, TypeError):
                secs = wait_default
            time.sleep(secs)

        elif action_type in ("COMPLETE", "ABORT", "INFO"):
            pass  # caller handles these

        else:
            print(f"[Agent] Unhandled action type: {action_type}")

        print(f"[Timing] execute {action_type}: {time.time()-t0:.3f}s")

    # ── Pre-loop: detect and launch target app ─────────────────────────────────

    def _pre_launch_app(self, task: str, session_dir: str) -> None:
        """Press HOME, screenshot, ask LLM which app to open, then launch it."""
        t_start = time.time()
        print("[Agent] Pre-loop: detecting target app...")

        # 1. Press HOME to return to desktop
        try:
            self._post("/key", {"keycode": 3})
            time.sleep(self.agent_cfg.get("pre_launch_home_sleep", 1.0))
        except Exception as e:
            print(f"[Agent] HOME key failed: {e}")

        # 2. Screenshot the home screen
        try:
            image_bytes, data_url = self._screenshot()
            sc_path = os.path.join(session_dir, "pre_launch.png")
            with open(sc_path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            print(f"[Agent] Pre-launch screenshot failed: {e}")
            return

        # 3. Ask LLM which app to open
        t0 = time.time()
        try:
            messages = build_app_detection_messages(task, data_url)
            response_text = ask_llm(messages, self.llm_cfg)
            print(f"[Agent] App detection response: {response_text.strip()}")
            print(f"[Timing] pre-launch LLM: {time.time()-t0:.3f}s")
        except Exception as e:
            print(f"[Agent] App detection LLM call failed: {e}")
            return

        # 4. Parse the AWAKE action to get app name
        try:
            action = str2action(response_text)
            app_name = action.get("value", "").strip()
        except Exception as e:
            print(f"[Agent] App detection parse failed: {e}, skipping launch")
            return

        if not app_name:
            print("[Agent] No specific app to launch, entering main loop directly")
            return

        # 5. Launch the app
        print(f"[Agent] Pre-launching app: {app_name}")
        try:
            self._post("/launch", {"app_name": app_name, "reflush": True})
            time.sleep(self.agent_cfg.get("post_launch_sleep", 2.0))
        except Exception as e:
            print(f"[Agent] Pre-launch app failed: {e}")

        print(f"[Timing] pre-launch total: {time.time()-t_start:.3f}s")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self, task: str) -> dict:
        max_steps = self.agent_cfg.get("max_steps", 50)
        delay = self.agent_cfg.get("delay_before_screenshot", 0.5)
        reflush_app = self.agent_cfg.get("reflush_app", True)
        log_dir = self.agent_cfg.get("log_dir", "./mc_logs")
        think = self.agent_cfg.get("think", True)

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        step = 0
        session_dir = os.path.join(log_dir, session_id)
        os.makedirs(session_dir, exist_ok=True)
        print(f"[Agent] Session: {session_id}  Task: {task}")

        # Wake screen
        try:
            self._post("/wake_screen", {})
        except Exception as e:
            print(f"[Agent] wake_screen failed: {e}")

        # Pre-loop: detect and launch target app
        self._pre_launch_app(task, session_dir)

        summary_history = ""
        qa_pairs: list = []
        stop_reason = "MAX_STEPS_REACHED"

        for step in range(1, max_steps + 1):
            step_t0 = time.time()
            print(f"\n[Agent] ── Step {step}/{max_steps} ──────────────────────────")

            time.sleep(delay)

            # Screenshot
            t0 = time.time()
            try:
                image_bytes, data_url = self._screenshot()
            except Exception as e:
                print(f"[Agent] Screenshot failed: {e}")
                stop_reason = "SCREENSHOT_ERROR"
                break
            sc_elapsed = time.time() - t0

            # Save screenshot
            sc_path = os.path.join(session_dir, f"step_{step:03d}.png")
            with open(sc_path, "wb") as f:
                f.write(image_bytes)

            # Build messages and call LLM
            messages = build_messages(task, data_url, summary_history, qa_pairs, think=think)
            t0 = time.time()
            try:
                response_text = ask_llm(messages, self.llm_cfg)
            except Exception as e:
                import requests as _req
                detail = e.response.text if isinstance(e, _req.HTTPError) and e.response is not None else ""
                print(f"[Agent] LLM call failed: {e}\n{detail}")
                stop_reason = "LLM_ERROR"
                break
            llm_elapsed = time.time() - t0

            # Parse response
            t0 = time.time()
            try:
                action = str2action(response_text, think=think)
            except Exception as e:
                print(f"[Agent] Parse failed: {e}\nRaw: {response_text[:300]}")
                stop_reason = "PARSE_ERROR"
                break
            parse_elapsed = time.time() - t0

            action_type = action.get("action", "")
            print(f"[Agent] Action: {action_type}  explain: {action.get('explain', '')}")
            print(f"[Timing] Step {step}: screenshot={sc_elapsed:.3f}s  llm={llm_elapsed:.3f}s  parse={parse_elapsed:.3f}s")

            # Save step log
            log_entry = {
                "step": step,
                "action": action,
                "raw_response": response_text,
            }
            with open(os.path.join(session_dir, f"step_{step:03d}.json"), "w", encoding="utf-8") as f:
                json.dump(log_entry, f, ensure_ascii=False, indent=2)

            # Update summary
            summary_history = action.get("summary", summary_history)

            # Terminal actions
            if action_type == "COMPLETE":
                print(f"[Agent] COMPLETE: {action.get('return', '')}")
                stop_reason = "COMPLETE"
                break

            if action_type == "ABORT":
                print(f"[Agent] ABORT: {action.get('value', '')}")
                stop_reason = "ABORT"
                break

            if action_type == "INFO":
                question = action.get("value", "")
                print(f"[Agent] INFO question: {question}")
                answer = input(f"Agent asks: {question}\nYour answer: ").strip()
                qa_pairs.append((question, answer))
                continue

            # Execute action
            t0 = time.time()
            try:
                self._execute(action, reflush_app=reflush_app)
            except Exception as e:
                print(f"[Agent] Execute failed: {e}")
            exec_elapsed = time.time() - t0
            step_elapsed = time.time() - step_t0
            print(f"[Timing] Step {step} total={step_elapsed:.3f}s (exec={exec_elapsed:.3f}s)")

        result = {
            "session_id": session_id,
            "task": task,
            "stop_reason": stop_reason,
            "steps": step if stop_reason != "MAX_STEPS_REACHED" else max_steps,
            "log_dir": session_dir,
        }
        print(f"\n[Agent] Done. stop_reason={stop_reason}, steps={result['steps']}")
        return result
