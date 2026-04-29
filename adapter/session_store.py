"""Session persistence for mobile GUI tasks."""
import json
import os
import uuid
from datetime import datetime

SESSIONS_DIR = os.path.expanduser("~/.openclaw/mobile_gui/tasks")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def create_session(goal: str, config: dict) -> dict:
    _ensure_dir()
    task_id = "task_" + uuid.uuid4().hex[:8]
    session = {
        "task_id": task_id,
        "goal": goal,
        "status": "running",
        "step_index": 0,
        "pending_question": None,
        "resume_token": None,
        "summary_history": "",
        "qa_pairs": [],
        "config": config,
        "created_at": datetime.now().isoformat(),
    }
    _save(session)
    return session


def load_session(task_id: str) -> dict | None:
    path = os.path.join(SESSIONS_DIR, f"{task_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_session(session: dict) -> None:
    _ensure_dir()
    _save(session)


def _save(session: dict) -> None:
    path = os.path.join(SESSIONS_DIR, f"{session['task_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def delete_session(task_id: str) -> None:
    path = os.path.join(SESSIONS_DIR, f"{task_id}.json")
    if os.path.exists(path):
        os.unlink(path)
