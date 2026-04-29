# Release Checklist

Use this checklist before publishing `mobile-gui-bundle` to ClawHub or distributing it as a local bundle.

## Structure

- [ ] `.codex-plugin/plugin.json` exists and contains the correct bundle name/version
- [ ] `.mcp.json` exists and uses `mcpServers` with a stdio `command` and `args`
- [ ] `skills/mobile_gui/SKILL.md` exists
- [ ] `dist/bundle.js` exists
- [ ] `adapter/` includes `bridge.py`, `adb_bridge.py`, and `yadb`
- [ ] `scripts/start_bridge.sh` exists
- [ ] `config.example.yaml` exists
- [ ] `README.md` and `LICENSE` exist

## Safety And Installability

- [ ] `openclaw plugins inspect <bundle-id> --json` shows `format: bundle`
- [ ] `bundleCapabilities` includes `skills` and `mcpServers`
- [ ] `.mcp.json` resolves to `hasStdioTransport: true`
- [ ] README documents `--dangerously-force-unsafe-install`
- [ ] README explains why dangerous install is required

## Runtime Preconditions

- [ ] Host has `adb`, `node`, and `python3`
- [ ] Python runtime used by the bundle has `flask`, `requests`, `pyyaml`, and `Pillow`
- [ ] `adb devices` shows at least one connected Android device
- [ ] `config.yaml` can be created from `config.example.yaml`

## Functional Verification

- [ ] `bash scripts/start_bridge.sh` starts the bridge without path errors
- [ ] MCP `tools/list` returns the mobile GUI tools
- [ ] MCP `mobile_device_status` returns `adb_connected: true`
- [ ] MCP `mobile_gui_observe` returns `status: ok` with a screenshot path
- [ ] High-risk confirmation flow is still documented in `SKILL.md`

## Packaging Discipline

- [ ] Original `mobile_gui_plugin/` is untouched
- [ ] Bundle contains only runtime artifacts and release docs
- [ ] Native `openclaw.plugin.json` is not present in the bundle root
- [ ] Any rebuilt `dist/bundle.js` has been copied into this bundle intentionally
