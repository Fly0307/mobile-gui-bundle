# mobile-gui-bundle

Android GUI automation bundle for OpenClaw.

This bundle provides:

- a skill: `mobile_gui`
- MCP tools for Android GUI automation
- a Python bridge and ADB bridge for device interaction

## Requirements

Host environment:

- Linux
- `adb`
- `node`
- `python3`

Python packages required by the bridge:

- `flask`
- `requests`
- `pyyaml`
- `pillow`

Android-side assumptions:

- an Android device is connected through ADB
- the included `adapter/yadb` can be pushed to the device when needed

## Configuration

Create a runtime config file from the example:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` and fill at least:

- `llm.api_base`
- `llm.api_key`
- `llm.model_name`

Optional:

- `adb.device`
- `llm.image_resize`

## Start the ADB bridge

Run from the bundle root:

```bash
bash scripts/start_bridge.sh
```

This starts the local HTTP bridge used by the MCP server.

## Install

Local directory install:

```bash
openclaw plugins install ./mobile-gui-bundle --dangerously-force-unsafe-install
```

ClawHub install:

```bash
openclaw plugins install clawhub:mobile-gui-bundle --dangerously-force-unsafe-install
```

The current MCP bridge implementation spawns a local Python subprocess and is
expected to trigger OpenClaw's dangerous-code scanner. Treat this bundle as a
trusted local/operator-managed plugin and review the shipped files before
installing it.

After install, restart the gateway:

```bash
openclaw gateway restart
```

## Verify

Ask the agent to list available tools and confirm the following are present:

- `mobile_device_status`
- `mobile_gui_observe`
- `mobile_gui_start_task`
- `mobile_gui_resume_task`
- `mobile_gui_setup`
- `mobile_gui_cancel_task`

Then test with a simple task such as:

- “打开设置查看手机型号”

## Notes

- High-risk tasks such as messaging, payment, or deletion should require explicit user confirmation.
- The bundle ships the skill and MCP server, but runtime environment setup is still the operator’s responsibility.
