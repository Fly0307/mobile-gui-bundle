"""
Wraps AgentLoop with pause-on-INFO support.
All dependencies are local to this adapter/ directory.
"""
import os
import sys
import time
import uuid

# Ensure adapter/ itself is on the path so local imports resolve
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from agent_loop import AgentLoop          # noqa: E402 (local copy)
from action_parser import build_app_detection_messages, build_messages, str2action  # noqa: E402
from llm_client import ask_llm            # noqa: E402


class PausableAgentLoop(AgentLoop):
    def run_pausable(self, task: str, session: dict, debug: bool = False) -> dict:
        def _dbg(*args):
            if debug:
                print("[DEBUG]", *args, file=sys.stderr, flush=True)
        max_steps = self.agent_cfg.get("max_steps", 50)
        delay = self.agent_cfg.get("delay_before_screenshot", 0.5)
        reflush_app = self.agent_cfg.get("reflush_app", True)
        log_dir = self.agent_cfg.get("log_dir", "./mc_logs")
        post_launch_sleep = self.agent_cfg.get("post_launch_sleep", 2.0)
        pre_launch_home_sleep = self.agent_cfg.get("pre_launch_home_sleep", 1.0)
        think = self.agent_cfg.get("think", True)
        # Resolve relative log_dir against adapter/ so it's cwd-independent
        if not os.path.isabs(log_dir):
            log_dir = os.path.join(_HERE, log_dir)

        session_dir = os.path.join(log_dir, session["task_id"])
        os.makedirs(session_dir, exist_ok=True)

        qa_pairs = [tuple(p) for p in session.get("qa_pairs", [])]
        summary_history = session.get("summary_history", "")
        start_step = session.get("step_index", 0)

        if start_step == 0:
            try:
                self._post("/wake_screen", {})
            except Exception:
                pass

            # Pre-launch: press HOME, screenshot, ask LLM which app to open
            _dbg("Pre-loop: detecting target app...")
            try:
                self._post("/key", {"keycode": 3})
                time.sleep(pre_launch_home_sleep)
            except Exception:
                pass

            try:
                _, data_url = self._screenshot()
                messages = build_app_detection_messages(task, data_url)
                response_text = ask_llm(messages, self.llm_cfg)
                _dbg(f"App detection response: {response_text.strip()}")
                action = str2action(response_text)  # app detection always parses without think
                app_name = action.get("value", "").strip()
                if app_name:
                    _dbg(f"Pre-launching app: {app_name}")
                    self._post("/launch", {"app_name": app_name, "reflush": True})
                    time.sleep(post_launch_sleep)
                else:
                    _dbg("No specific app to launch, entering main loop directly")
            except Exception as e:
                _dbg(f"Pre-loop app detection failed: {e}")

        step = start_step
        for step in range(start_step + 1, max_steps + 1):
            step_t0 = time.time()
            _dbg(f"─── Step {step}/{max_steps} ───")
            time.sleep(delay)

            try:
                t0 = time.time()
                _, data_url = self._screenshot()
                _dbg(f"Screenshot OK ({time.time()-t0:.3f}s)")
            except Exception as e:
                return {"status": "failed", "error": f"Screenshot failed: {e}"}

            messages = build_messages(task, data_url, summary_history, qa_pairs, think=think)
            try:
                t0 = time.time()
                response_text = ask_llm(messages, self.llm_cfg)
                _dbg(f"LLM response cost={time.time()-t0:.3f}s\n{response_text}")
            except Exception as e:
                return {"status": "failed", "error": f"LLM failed: {e}"}

            try:
                t0 = time.time()
                action = str2action(response_text, think=think)
                _dbg(f"Parsed action cost={time.time()-t0:.3f}s: {action}")
            except Exception as e:
                return {"status": "failed", "error": f"Parse failed: {e}"}

            action_type = action.get("action", "")
            summary_history = action.get("summary", summary_history)

            if action_type == "INFO":
                question = action.get("value", "")
                resume_token = "resume_" + uuid.uuid4().hex[:8]
                return {
                    "status": "needs_user_input",
                    "question": question,
                    "question_type": "info_request",
                    "task_id": session["task_id"],
                    "resume_token": resume_token,
                    "checkpoint": {
                        "step_index": step,
                        "summary_history": summary_history,
                        "qa_pairs": [list(p) for p in qa_pairs],
                        "pending_question": question,
                        "resume_token": resume_token,
                    },
                }

            if action_type == "COMPLETE":
                return {"status": "completed", "return": action.get("return", ""), "steps": step}

            if action_type == "ABORT":
                return {"status": "failed", "error": action.get("value", "Task aborted"), "steps": step}

            try:
                t0 = time.time()
                self._execute(action, reflush_app=reflush_app)
                _dbg(f"Execute OK: {action_type} ({time.time()-t0:.3f}s)")
            except Exception as e:
                print(f"[PausableAgent] Execute failed: {e}", file=sys.stderr)

            _dbg(f"Step total cost={time.time()-step_t0:.3f}s")

        return {"status": "failed", "error": f"Max steps ({max_steps}) reached", "steps": step}
