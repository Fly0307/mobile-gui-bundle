"""
ADB Bridge: Flask HTTP server that wraps local ADB commands.
Exposes a REST API for device actions.  Runs on the device itself.

Coordinate convention: all endpoints accept [0, 1000] normalised coordinates.
"""
import base64 as _base64
import os
import re
import subprocess
import time

from flask import Flask, jsonify, request

try:
    from .package_map import find_package_name
except ImportError:
    from package_map import find_package_name  # type: ignore[no-redef]

app = Flask(__name__)

# ── Config (set via configure() before starting) ───────────────────────────────
_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))
_cfg: dict = {
    "device": "",
    "yadb_path": os.path.join(_ADAPTER_DIR, "yadb"),
    "local_tmp_dir": os.path.expanduser("~/.openclaw/mobile_gui/screenshots"),
    "override_size": None,
    "force_stop_sleep": 1.0,
    "tap_focus_sleep": 0.5,
}
_screen_size: tuple = (1080, 1920)  # (width, height), refreshed on startup
# Android-side path where yadb is pushed for app_process use
_ANDROID_YADB_PATH = "/data/local/tmp/yadb"


def configure(cfg: dict) -> None:
    _cfg.update(cfg)
    # Expand ~ and resolve relative paths so adb pull always works
    _cfg["local_tmp_dir"] = os.path.abspath(os.path.expanduser(_cfg["local_tmp_dir"]))
    # If yadb_path is empty or relative, resolve against adapter dir
    yadb = _cfg.get("yadb_path", "")
    if not yadb or not os.path.isabs(yadb):
        if yadb:
            yadb = os.path.join(_ADAPTER_DIR, yadb)
        else:
            yadb = os.path.join(_ADAPTER_DIR, "yadb")
        _cfg["yadb_path"] = yadb
    _screen_size_refresh()


# ── ADB helpers ────────────────────────────────────────────────────────────────

def _adb(*args) -> subprocess.CompletedProcess:
    device = _cfg.get("device", "")
    if device:
        cmd = ["adb", "-s", device] + list(args)
    else:
        cmd = ["adb"] + list(args)
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"[ADB] {' '.join(str(a) for a in args[:3])}: {time.time()-t0:.3f}s rc={result.returncode}")
    return result


def _adb_binary(*args) -> subprocess.CompletedProcess:
    """Like _adb but captures raw bytes (for binary output like screencap)."""
    device = _cfg.get("device", "")
    if device:
        cmd = ["adb", "-s", device] + list(args)
    else:
        cmd = ["adb"] + list(args)
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True)
    print(f"[ADB] {' '.join(str(a) for a in args[:3])}: {time.time()-t0:.3f}s rc={result.returncode} bytes={len(result.stdout)}")
    return result


def _adb_shell(*args) -> subprocess.CompletedProcess:
    return _adb("shell", *args)


def _to_px(norm_x: int, norm_y: int) -> tuple:
    """Convert [0,1000] normalised coords to pixels, respecting orientation."""
    orientation = _detect_orientation()
    override = _cfg.get("override_size")
    if isinstance(override, (list, tuple)) and len(override) == 2:
        w, h = int(override[0]), int(override[1])
    else:
        w, h = _screen_size
    if orientation in (1, 3):
        w, h = h, w
    return int(norm_x / 1000 * w), int(norm_y / 1000 * h)


def _detect_orientation() -> int:
    device = _cfg.get("device", "")
    prefix = f"adb -s {device}" if device else "adb"
    cmd = (
        f'{prefix} shell dumpsys input | grep -m 1 -o -E "orientation=[0-9]"'
        " | grep -m 1 -o -E '[0-9]'"
    )
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        return int(res.stdout.strip())
    except ValueError:
        return 0


def _screen_size_refresh() -> tuple:
    global _screen_size
    res = _adb_shell("wm", "size")
    out = res.stdout.strip()
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        _screen_size = (int(m.group(1)), int(m.group(2)))
    return _screen_size


def _push_yadb_to_device() -> bool:
    """Push local yadb to Android device at _ANDROID_YADB_PATH. Returns True on success."""
    local_yadb = _cfg["yadb_path"]
    if not os.path.exists(local_yadb):
        print(f"[Bridge] yadb push skipped: local file not found at {local_yadb}")
        return False
    r = _adb("push", local_yadb, _ANDROID_YADB_PATH)
    print(f"[Bridge] yadb push rc={r.returncode} stderr={r.stderr.strip()!r}")
    if r.returncode != 0:
        return False
    _adb_shell("chmod", "755", _ANDROID_YADB_PATH)
    return True


def _yadb_available() -> bool:
    """Check if yadb is accessible on the Android side (not just in rootfs)."""
    res = _adb_shell("ls", _ANDROID_YADB_PATH)
    available = "No such file" not in res.stdout and res.returncode == 0
    print(f"[Bridge] yadb android-side check: path={_ANDROID_YADB_PATH} available={available} rc={res.returncode} out={res.stdout.strip()!r}")
    return available


def _get_yadb_path() -> str:
    """Return the Android-side yadb path for use in app_process commands."""
    return _ANDROID_YADB_PATH


# ── Device init ────────────────────────────────────────────────────────────────

def init_device() -> dict:
    """Check yadb and screen size."""
    yadb = _cfg["yadb_path"]
    if os.path.exists(yadb):
        print(f"[Bridge] yadb local OK ({yadb})")
        # Push to Android device so app_process can use it
        if _push_yadb_to_device():
            print(f"[Bridge] yadb pushed to Android: {_ANDROID_YADB_PATH}")
        else:
            print(f"[Bridge] WARNING: yadb push failed. TYPE/LONGPRESS may fail.")
    else:
        print(f"[Bridge] WARNING: yadb not found at {yadb}. TYPE/LONGPRESS may fail.")

    # local_tmp_dir lives in the sandbox filesystem (not on the ADB device)
    os.makedirs(_cfg["local_tmp_dir"], exist_ok=True)
    print(f"[Bridge] local_tmp_dir={_cfg['local_tmp_dir']}")

    size = _screen_size_refresh()
    print(f"[Bridge] Screen size: {size[0]}x{size[1]}")
    return {"screen_size": {"width": size[0], "height": size[1]}}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})


@app.route("/screen_size", methods=["GET"])
def screen_size():
    w, h = _screen_size_refresh()
    return jsonify({"width": w, "height": h})


@app.route("/screenshot", methods=["GET"])
def screenshot():
    """Capture screenshot. Returns {image: <base64 PNG>, width, height}."""
    t0 = time.time()
    try:
        # Use exec-out to stream PNG directly — avoids cross-namespace /tmp issues
        r = _adb_binary("exec-out", "screencap", "-p")
        print(f"[screenshot] exec-out screencap rc={r.returncode} bytes={len(r.stdout)} stderr={r.stderr!r}")
        if r.returncode != 0 or len(r.stdout) < 100:
            msg = f"screencap failed: {r.stderr.decode(errors='replace').strip() or 'empty output'}"
            print(f"[screenshot] ERROR: {msg}")
            return jsonify({"error": msg}), 500

        t1 = time.time()
        encoded = _base64.b64encode(r.stdout).decode()
        w, h = _screen_size
        print(f"[Timing] /screenshot: adb={t1-t0:.3f}s encode={time.time()-t1:.3f}s total={time.time()-t0:.3f}s")
        return jsonify({"image": encoded, "width": w, "height": h})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/tap", methods=["POST"])
def tap():
    """Tap at normalised [0,1000] coords. Body: {x, y}"""
    t0 = time.time()
    body = request.get_json(force=True)
    px, py = _to_px(int(body["x"]), int(body["y"]))
    r = _adb_shell("input", "tap", str(px), str(py))
    print(f"[tap] px={px} py={py} rc={r.returncode} total={time.time()-t0:.3f}s stderr={r.stderr!r}")
    return jsonify({"x": px, "y": py})


@app.route("/longpress", methods=["POST"])
def longpress():
    """Long-press at normalised coords. Body: {x, y, duration_ms?}"""
    t0 = time.time()
    body = request.get_json(force=True)
    px, py = _to_px(int(body["x"]), int(body["y"]))
    duration_ms = int(body.get("duration_ms", 1500))

    if _yadb_available():
        yadb = _get_yadb_path()
        device = _cfg.get("device", "")
        prefix = f"adb -s {device}" if device else "adb"
        cmd = (
            f"{prefix} shell app_process "
            f"-Djava.class.path={yadb} {os.path.dirname(yadb)} "
            f"com.ysbing.yadb.Main -touch {px} {py} {duration_ms}"
        )
        t1 = time.time()
        r = subprocess.run(cmd, shell=True, capture_output=True)
        print(f"[longpress] yadb px={px} py={py} rc={r.returncode} cmd={time.time()-t1:.3f}s total={time.time()-t0:.3f}s stderr={r.stderr!r}")
    else:
        r = _adb_shell("input", "swipe", str(px), str(py), str(px), str(py), str(duration_ms))
        print(f"[longpress] swipe-fallback px={px} py={py} rc={r.returncode} total={time.time()-t0:.3f}s stderr={r.stderr!r}")
    return jsonify({"x": px, "y": py, "duration_ms": duration_ms})


_ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"


def _type_via_adbkeyboard(text: str) -> bool:
    """Input text via ADBKeyboard (supports Chinese/Unicode). Returns True on success."""
    t0 = time.time()
    print(f"[type] ADBKeyboard: starting, text={text!r}")

    # 1. Save current IME
    r = _adb_shell("settings", "get", "secure", "default_input_method")
    current_ime = r.stdout.strip()
    print(f"[type] ADBKeyboard: current_ime={current_ime} rc={r.returncode}")

    # 2. Enable ADBKeyboard
    r = _adb_shell("ime", "enable", _ADB_KEYBOARD_IME)
    print(f"[type] ADBKeyboard: enable rc={r.returncode} stderr={r.stderr.strip()!r}")

    # 3. Switch to ADBKeyboard via ime set (different permission path than settings put)
    r = _adb_shell("ime", "set", _ADB_KEYBOARD_IME)
    print(f"[type] ADBKeyboard: set rc={r.returncode} stderr={r.stderr.strip()!r}")
    time.sleep(0.4)

    # 4. Verify switch took effect
    r = _adb_shell("settings", "get", "secure", "default_input_method")
    active_ime = r.stdout.strip()
    print(f"[type] ADBKeyboard: active_ime after switch={active_ime}")
    if _ADB_KEYBOARD_IME not in active_ime:
        print("[type] ADBKeyboard: switch failed, restoring and aborting")
        _adb_shell("ime", "set", current_ime)
        return False

    # 5. Send text as base64
    b64 = _base64.b64encode(text.encode("utf-8")).decode("utf-8")
    r = _adb_shell("am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b64)
    print(f"[type] ADBKeyboard: input rc={r.returncode} stderr={r.stderr.strip()!r}")
    time.sleep(0.4)

    # 6. Restore original IME
    _adb_shell("ime", "set", current_ime)
    print(f"[type] ADBKeyboard: restored to {current_ime} total={time.time()-t0:.3f}s")

    return True


@app.route("/type", methods=["POST"])
def type_text():
    """Type text. Body: {text, x?, y?}  x/y optional (tap to focus first)."""
    t0 = time.time()
    body = request.get_json(force=True)
    text = body["text"]

    if body.get("x") is not None and body.get("y") is not None:
        px, py = _to_px(int(body["x"]), int(body["y"]))
        _adb_shell("input", "tap", str(px), str(py))
        time.sleep(_cfg.get("tap_focus_sleep", 0.5))

    escaped = text.replace(" ", "\\ ").replace("\n", " ").replace("\t", " ")

    typed = False

    # Method 1: yadb
    if _yadb_available():
        yadb = _get_yadb_path()
        device = _cfg.get("device", "")
        prefix = f"adb -s {device}" if device else "adb"
        cmd = (
            f"{prefix} shell app_process "
            f"-Djava.class.path={yadb} {os.path.dirname(yadb)} "
            f"com.ysbing.yadb.Main -keyboard '{escaped}'"
        )
        t1 = time.time()
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        print(f"[type] yadb text={text!r} rc={r.returncode} cmd={time.time()-t1:.3f}s stderr={r.stderr!r}")
        if r.returncode == 0:
            typed = True
        else:
            print("[Bridge] yadb failed, falling back to ADBKeyboard")

    # Method 2: ADBKeyboard (supports Chinese/Unicode)
    if not typed:
        typed = _type_via_adbkeyboard(text)
    print(f"[Timing] /type total={time.time()-t0:.3f}s typed={typed}")
    return jsonify({"text": text})


@app.route("/swipe", methods=["POST"])
def swipe():
    """Swipe. Body: {x1, y1, x2, y2, duration_ms?}"""
    t0 = time.time()
    body = request.get_json(force=True)
    x1, y1 = _to_px(int(body["x1"]), int(body["y1"]))
    x2, y2 = _to_px(int(body["x2"]), int(body["y2"]))
    duration_ms = int(body.get("duration_ms", 1200))
    r = _adb_shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    print(f"[swipe] ({x1},{y1})->({x2},{y2}) rc={r.returncode} total={time.time()-t0:.3f}s stderr={r.stderr!r}")
    return jsonify({"x1": x1, "y1": y1, "x2": x2, "y2": y2})


@app.route("/key", methods=["POST"])
def key():
    """Send keyevent. Body: {keycode} or {key: 'home'|'back'|'power'|'volume_up'|'volume_down'}"""
    body = request.get_json(force=True)
    key_map = {
        "home": 3, "back": 4, "power": 26,
        "volume_up": 24, "volume_down": 25, "menu": 82,
    }
    if "keycode" in body:
        code = int(body["keycode"])
    else:
        code = key_map[body["key"].lower()]
    r = _adb_shell("input", "keyevent", str(code))
    print(f"[key] keycode={code} rc={r.returncode} stderr={r.stderr!r}")
    return jsonify({"keycode": code})


@app.route("/launch", methods=["POST"])
def launch():
    """Launch an app. Body: {app_name, reflush?}"""
    t0 = time.time()
    body = request.get_json(force=True)
    app_name = body["app_name"]
    reflush = body.get("reflush", True)
    package = find_package_name(app_name)
    if not package:
        msg = f"No package found for app '{app_name}'"
        print(f"[launch] ERROR: {msg}")
        return jsonify({"error": msg}), 400

    if reflush:
        _adb_shell("am", "force-stop", package)
        time.sleep(_cfg.get("force_stop_sleep", 1.0))

    r = _adb_shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
    print(f"[launch] app={app_name} pkg={package} rc={r.returncode} total={time.time()-t0:.3f}s stderr={r.stderr!r}")
    return jsonify({"app_name": app_name, "package": package})


@app.route("/wake_screen", methods=["POST"])
def wake_screen():
    """Wake screen if it is off."""
    res = _adb_shell("dumpsys", "display")
    is_on = "ON" in res.stdout
    print(f"[wake_screen] is_on={is_on}")
    if not is_on:
        _adb_shell("input", "keyevent", "26")  # POWER
        time.sleep(0.3)
        w, h = _screen_size
        _adb_shell("input", "swipe", str(w // 2), str(int(h * 0.9)),
                   str(w // 2), str(int(h * 0.2)))
    return jsonify({"was_on": is_on})


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_server(host: str = "127.0.0.1", port: int = 8765, debug: bool = True) -> None:
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    import yaml as _yaml
    _cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    if os.path.exists(_cfg_path):
        with open(_cfg_path, encoding="utf-8") as _f:
            _full = _yaml.safe_load(_f)
        configure(_full.get("adb", {}))
        _bridge = _full.get("bridge", {})
        _host = _bridge.get("host", "127.0.0.1")
        _port = int(_bridge.get("port", 8765))
    else:
        _host, _port = "127.0.0.1", 8765
    init_device()
    print(f"[Bridge] Listening on {_host}:{_port}")
    run_server(host=_host, port=_port)
