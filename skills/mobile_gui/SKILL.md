---
name: mobile_gui
description: >
  在已连接的 Android 手机上执行 GUI 自动化任务，例如发消息、打开设置、在 App 内完成操作。
  当用户明确要求“帮我在手机上操作”时使用。本 skill 不用于纯信息查询、电脑端操作或代码任务。
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["adb", "node", "python3"]
---

# mobile_gui

## When To Run

当用户要求在 Android 手机上执行实际 GUI 操作时使用，例如：

- 帮我给某人发微信或短信
- 在某个 App 内完成步骤
- 打开设置查看信息
- 在手机 App 内创建记录、待办或草稿

不要用于：

- 纯信息问答
- 电脑端网页或桌面操作
- 代码编辑、文件管理、文档撰写

## Preconditions

在执行任务前，优先检查以下条件：

1. `mobile_device_status {}`
2. 若返回 `needs_setup`，进入初始化流程
3. 若 `adb_connected != true`，告知用户先连接 ADB 设备
4. 若 bridge 未启动或返回连接错误，提示用户先启动 bundle 内的 `scripts/start_bridge.sh`

## Safe Execution Policy

以下任务在执行前必须向用户二次确认：

- 发送消息
- 下单、支付、转账
- 删除内容
- 提交或发布不可撤销的信息

确认话术应包含：

- 将执行的平台或 App
- 目标对象
- 关键内容或动作

示例：
`我将帮你在微信给张三发送“明天见”，确认执行吗？`

若用户未明确确认，不要调用 `mobile_gui_start_task`。

## Standard Flow

```json
mobile_device_status {}
```

- 若 `status == "needs_setup"`：进入 setup flow
- 若 `adb_connected != true`：停止并提示连接设备
- 若 bridge 连接失败：提示先启动 bridge
- 否则继续

对于低风险任务，直接开始：

```json
mobile_gui_start_task {
  "goal": "<user goal>",
  "max_steps": 30
}
```

处理返回值：

- `status == "completed"`：向用户汇报完成结果
- `status == "failed"`：向用户汇报失败原因
- `status == "needs_user_input"`：进入 resume flow

## Resume Flow

当任务返回：

```json
{
  "status": "needs_user_input",
  "question": "...",
  "task_id": "...",
  "resume_token": "..."
}
```

你必须：

1. 保留 `task_id`
2. 保留 `resume_token`
3. 直接向用户转述问题
4. 等待用户回答
5. 调用：

```json
mobile_gui_resume_task {
  "task_id": "<task_id>",
  "resume_token": "<resume_token>",
  "user_response": "<user response>"
}
```

重复直到任务 `completed` 或 `failed`。

## Setup Flow

当任一工具返回 `status: "needs_setup"` 时：

1. 告知用户插件需要初始化
2. 收集必填字段：
   - `llm.api_base`
   - `llm.api_key`
   - `llm.model_name`
3. 必要时收集可选字段：
   - `adb.device`
   - `llm.image_resize`
4. 调用：

```json
mobile_gui_setup {
  "llm.api_base": "<value>",
  "llm.api_key": "<value>",
  "llm.model_name": "<value>",
  "adb.device": "<optional value>"
}
```

若返回 `incomplete`，继续补齐缺失字段。
若返回 `ok`，告知用户初始化完成并继续任务。

## Observe Tool

`mobile_gui_observe` 仅用于：

- 调试当前屏幕状态
- 在用户要求先看当前界面时辅助判断
- 排查是否停在错误页面

不要把 `observe` 当作标准主流程中的必经步骤，除非确有必要。

## Cancel Flow

若用户要求取消当前任务，调用：

```json
mobile_gui_cancel_task {
  "task_id": "<task_id>"
}
```

## Notes

- 每个 `task_id` 对应一个持久化 session
- 需要恢复任务时必须使用原始 `resume_token`
- 对高风险任务，确认优先级高于效率
