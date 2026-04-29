# mobile-gui-bundle 使用说明

本文档是 `mobile-gui-bundle` 的主要部署与使用说明，面向两类读者：

- OpenClaw 普通使用者：需要完成安装、配置、启动与验证
- 维护者/发布者：需要从 `mobile_gui_plugin/` 重建并同步发布 bundle

## 这是什么

`mobile-gui-bundle` 是一个面向 OpenClaw 的 bundle 形式插件。

它把三层能力打包在一起：

- 一个 skill：`mobile_gui`
- 一个 MCP stdio 服务：`dist/bundle.js`
- 一个 Python 与 ADB 运行时桥接层：`adapter/`

整体工作流如下：

1. OpenClaw 加载 bundle 中的 skill 与 MCP 配置
2. MCP 服务接收来自 OpenClaw 的工具调用
3. MCP 服务调用 Python bridge
4. Python bridge 与本地 ADB bridge 以及配置的 LLM 接口通信
5. Android 设备通过 ADB 被观测与控制

## 它不是什么

这不是一个 OpenClaw 原生 SDK 插件。

它发布的是一个 bundle，主要暴露两类能力：

- `skills`
- `mcpServers`

这也是为什么此目录使用 `.codex-plugin/plugin.json`，而不是 `openclaw.plugin.json`。

OpenClaw 当前对 bundle 的识别入口是 `.codex-plugin/plugin.json`。如果根目录存在 `openclaw.plugin.json`，OpenClaw 会优先把该目录当作 native plugin 路径解析，而不是 bundle。

## 支持环境

宿主机环境要求：

- Linux
- `adb`
- `node`
- `python3`

Python 运行时依赖：

- `flask`
- `requests`
- `pyyaml`
- `Pillow`

Android 设备要求：

- 至少有一台 Android 设备通过 ADB 连接
- bundle 内自带的 `adapter/yadb` 可以按需推送到 `/data/local/tmp/yadb`

## 为什么安装时需要危险安装确认

当前 bundle 安装时需要：

```bash
--dangerously-force-unsafe-install
```

这是预期行为，不是异常。

原因是此 bundle 的运行方式会触发 OpenClaw 的危险代码扫描规则，例如：

- Node.js 从本地拉起 Python 子进程
- 读取环境变量作为运行时配置
- 通过本地网络请求与 ADB bridge 通信

这些行为对本 bundle 来说是合理且必要的，但在 OpenClaw 的安全模型里会被标记为高风险模式。因此你应将它视为“受信任的本地/运维管理插件”，在安装前先审阅所附带的文件。

## 目录结构说明

bundle 中的关键文件如下：

- `.codex-plugin/plugin.json`：bundle 清单
- `.mcp.json`：OpenClaw 的 MCP 服务注册文件
- `skills/mobile_gui/SKILL.md`：给 agent 使用的 skill 说明
- `dist/bundle.js`：MCP stdio 服务入口
- `adapter/`：Python 运行时和 ADB bridge
- `scripts/start_bridge.sh`：启动 ADB bridge 的辅助脚本
- `config.example.yaml`：运行时配置模板
- `RELEASE_CHECKLIST.md`：发布前检查清单

## 在 OpenClaw 中安装

### 方式一：从本地目录安装

在仓库根目录执行：

```bash
openclaw plugins install ./mobile-gui-bundle --dangerously-force-unsafe-install
```

### 方式二：从 ClawHub 安装

```bash
openclaw plugins install clawhub:mobile-gui-bundle --dangerously-force-unsafe-install
```

安装后执行：

```bash
openclaw gateway restart
```

## 配置 bundle

先从模板生成运行时配置：

```bash
cd mobile-gui-bundle
cp config.example.yaml config.yaml
```

然后编辑 `config.yaml`，至少填写这些必填字段：

- `llm.api_base`
- `llm.api_key`
- `llm.model_name`

常见可选字段：

- `adb.device`
- `llm.image_resize`
- `agent.max_steps`
- `agent.delay_after_capture`

### 最小配置示例

```yaml
llm:
  api_base: "http://127.0.0.1:7003/v1"
  api_key: "EMPTY"
  model_name: "MobiMind-1.5-4B"
```

## Python 解释器选择规则

bundle 按以下顺序选择 Python：

1. `MOBILE_GUI_PYTHON`
2. bundle 根目录下的 `.venv/bin/python`
3. `python3`

如果你的系统 `python3` 没有安装 `flask` 等依赖，建议显式指定一个可用解释器：

```bash
export MOBILE_GUI_PYTHON=/path/to/python
```

例如：

```bash
export MOBILE_GUI_PYTHON=/usr/bin/python3
```

## 启动 ADB bridge

在 bundle 根目录执行：

```bash
bash scripts/start_bridge.sh
```

成功时输出类似：

```text
[start_bridge] Using Python: /usr/bin/python3
[start_bridge] Starting adb_bridge from .../mobile-gui-bundle/adapter ...
[Bridge] yadb OK
[Bridge] Screen size: 1240x2772
[Bridge] Listening on 127.0.0.1:8765
```

bridge 需要在使用期间持续运行。

## 验证 bundle 是否工作

### 1. 验证 OpenClaw 能识别 bundle

```bash
openclaw plugins inspect mobile-gui-bundle --json
```

你应该看到以下关键字段：

- `format: "bundle"`
- `bundleFormat: "codex"`
- `bundleCapabilities` 中包含 `skills` 与 `mcpServers`
- `mcpServers` 中包含 `mobile-gui`
- `hasStdioTransport: true`

### 2. 验证 ADB 连通性

```bash
adb devices
```

至少应有一台设备处于 `device` 状态。

### 3. 验证 MCP 服务能列出工具

在仓库根目录执行：

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' \
  | node mobile-gui-bundle/dist/bundle.js
```

预期工具包括：

- `mobile_device_status`
- `mobile_gui_observe`
- `mobile_gui_start_task`
- `mobile_gui_resume_task`
- `mobile_gui_setup`
- `mobile_gui_cancel_task`

### 4. 验证设备状态工具

```bash
printf '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mobile_device_status","arguments":{}}}\n' \
  | node mobile-gui-bundle/dist/bundle.js
```

成功时你应看到：

- `adb_connected: true`
- `plugin_available: true`
- `bridge_url: http://127.0.0.1:8765`

### 5. 验证截图能力

```bash
printf '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mobile_gui_observe","arguments":{}}}\n' \
  | node mobile-gui-bundle/dist/bundle.js
```

成功时你应看到：

- `status: "ok"`
- 一个真实的 `screenshot_path`
- 合法的 `width` 和 `height`

## 在 OpenClaw 中如何使用

安装并启动完成后，用户可以直接用自然语言调用，例如：

- 帮我打开设置查看手机型号
- 帮我在微信里给张三发消息
- 帮我在手机上创建一个待办

`mobile_gui` skill 负责：

- 判断何时使用这些工具
- 在高风险动作前强制二次确认
- 在任务暂停并要求用户补充信息时恢复任务

## 高风险操作

以下动作在执行前必须得到用户明确确认：

- 发送消息
- 支付、下单
- 转账
- 删除内容
- 提交不可撤销的信息

对应确认策略已经写在 `skills/mobile_gui/SKILL.md` 中。

## 暂停与恢复流程

如果任务返回 `needs_user_input`，agent 必须保留：

- `task_id`
- `resume_token`

然后把返回的 `question` 直接转述给用户，等待用户回答，再调用 `mobile_gui_resume_task` 继续任务。

## 常见问题与排查

### `adb devices` 看不到设备

检查：

- USB 或无线 ADB 连接状态
- 设备端是否弹出授权确认
- 执行 `adb kill-server && adb start-server`

### `scripts/start_bridge.sh` 提示 `config.yaml not found`

先创建配置文件：

```bash
cp config.example.yaml config.yaml
```

### `No module named 'flask'`

说明当前 Python 解释器缺少依赖。你可以：

```bash
export MOBILE_GUI_PYTHON=/path/to/python
```

或者为当前解释器安装所需依赖。

### `mobile_device_status` 返回 `adb_connected: false`

通常表示：

- ADB daemon 状态异常
- 没有已授权设备
- `adb.device` 配置指向了错误目标

### `mobile_gui_observe` 返回 bridge 连接错误

通常说明 HTTP bridge 尚未启动。执行：

```bash
bash scripts/start_bridge.sh
```

### bridge 已启动但截图仍然失败

检查：

- `adb devices` 是否仍然显示设备在线
- `adb shell screencap -p /sdcard/test.png`
- `adb pull /sdcard/test.png`

### 任务启动了，但 LLM 接口报错

检查：

- `llm.api_base`
- `llm.api_key`
- `llm.model_name`
- 当前机器是否能访问配置的 LLM 接口

## 维护者工作流

开发源码目录是：

- `mobile_gui_plugin/`

对外发布的 bundle 目录是：

- `mobile-gui-bundle/`

不要手工编辑这些派生文件：

- `mobile-gui-bundle/dist/bundle.js`
- 从源目录复制过来的运行时文件

除非你明确知道自己在做“仅发布包补丁”。

更稳妥的方式是：从源码目录重新构建并同步 bundle。

## 重新构建并同步 bundle

使用仓库根目录下的脚本：

```bash
bash scripts/rebuild_mobile_gui_bundle.sh
```

这个脚本会完成：

1. 重新构建 `mobile_gui_plugin/dist/bundle.js`
2. 复制运行时文件到 `mobile-gui-bundle/`
3. 刷新 `scripts/start_bridge.sh`
4. 刷新 `adapter/bridge.py`
5. 保留 bundle 自己维护的文件，例如：
   - `.codex-plugin/plugin.json`
   - `.mcp.json`
   - `skills/mobile_gui/SKILL.md`
   - `README.md`
   - `USAGE.md`
   - `RELEASE_CHECKLIST.md`
   - `LICENSE`

### 发布前最少需要验证的内容

请至少执行：

- `mobile-gui-bundle/RELEASE_CHECKLIST.md`

最低限度应确认：

- OpenClaw 能 inspect 出这个 bundle
- `.mcp.json` 被识别为 stdio MCP server
- `mobile_device_status` 可用
- `mobile_gui_observe` 可用

## 推荐的发布纪律

当你修改了源码目录后，建议按以下顺序操作：

1. 更新 `mobile_gui_plugin/`
2. 运行 `scripts/rebuild_mobile_gui_bundle.sh`
3. 重新验证 bundle 行为
4. 必要时更新发布说明或检查清单

这样可以让发布 bundle 保持可重复生成，避免源码目录与发布目录之间长期漂移。
