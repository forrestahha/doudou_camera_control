# Minimal Server Architecture

## 目标

这份说明定义一版最小可运行架构，用于把 OpenClaw 的拍摄工作流和服务端的拉流/重连职责分开。

适用前提：

- 接受短暂重连
- 不要求绝对不断流
- 先把素材稳定落到本地
- 服务端先只服务一台或少量 CB60

## 最小分工

OpenClaw 负责：

- 理解商家要拍什么
- 拆分 2 到 4 个镜头
- 减少换位置次数
- 告诉商家下一段该怎么放
- 调用服务端开始录制和查询结果

服务端负责：

- 获取或接收当前可用直播地址
- 拉取 HLS/HTTP-FLV 流
- 录制成 `.ts` 或 `.mp4`
- 断流后重连
- 查询设备状态、电量、在线情况
- 把录制结果保存到本地目录

## 最小接口

建议服务端先提供 5 个 HTTP 接口。

### 1. `POST /v1/capture-sessions`

作用：创建一次拍摄任务。

请求示例：

```json
{
  "device_serial": "BG4238078",
  "brief": "办公室工作情况, 办公室环境全景",
  "max_shots": 3
}
```

返回示例：

```json
{
  "session_id": "20260325-120000",
  "storage_root": "/data/captures/device-BG4238078/session-20260325-120000",
  "shots": [
    {
      "shot_id": "interior-wide",
      "label": "办公室环境全景",
      "duration_seconds": 15,
      "placement": "把设备放在办公室入口附近，朝向主要办公区域。"
    },
    {
      "shot_id": "staff-working",
      "label": "办公室工作情况",
      "duration_seconds": 15,
      "placement": "把设备放在工位区侧前方，能看到 2 到 4 个人在工作。"
    }
  ]
}
```

### 2. `GET /v1/capture-sessions/{session_id}`

作用：查询当前任务进度和下一段拍摄指令。

返回示例：

```json
{
  "session_id": "20260325-120000",
  "status": "capturing",
  "completed_count": 1,
  "total_count": 2,
  "next_shot": {
    "shot_id": "staff-working",
    "label": "办公室工作情况",
    "duration_seconds": 15,
    "placement": "把设备放在工位区侧前方，能看到 2 到 4 个人在工作。"
  }
}
```

### 3. `POST /v1/capture-sessions/{session_id}/shots/{shot_id}:record`

作用：录制当前镜头。

请求示例：

```json
{
  "stream_url": "https://open.ys7.com/v3/openlive/...m3u8",
  "duration_seconds": 15,
  "container": "mp4"
}
```

返回示例：

```json
{
  "ok": true,
  "session_id": "20260325-120000",
  "shot_id": "staff-working",
  "status": "captured",
  "output_path": "/data/captures/device-BG4238078/session-20260325-120000/02-staff-working.mp4",
  "captured_duration_seconds": 14.8,
  "next_instruction": "素材采集完成，请查看 storage_root。"
}
```

### 4. `POST /v1/devices/{device_serial}:health-check`

作用：查询设备是否在线，以及当前状态是否允许继续拍摄。

返回示例：

```json
{
  "ok": true,
  "device_online": true,
  "battery_percent": 96,
  "signal": 100,
  "defence": 1,
  "cloud_status": 2,
  "privacy_status": 0
}
```

### 5. `POST /v1/streams:refresh`

作用：当流断开时，重新获取新的直播地址。

请求示例：

```json
{
  "device_serial": "BG4238078",
  "channel_no": 1,
  "source": "device",
  "protocol": "hls"
}
```

返回示例：

```json
{
  "ok": true,
  "stream_url": "https://open.ys7.com/v3/openlive/...m3u8",
  "expires_at": "2026-03-25T19:00:00+08:00"
}
```

## OpenClaw 和服务端怎么配合

建议工作流如下：

1. OpenClaw 根据商家需求生成 `session`。
2. OpenClaw 读取 `next_shot`，告诉商家先把设备放哪里。
3. 商家放好设备后，OpenClaw 调 `record`。
4. 服务端开始录制。
5. 如果录制过程中断流，服务端先做 `health-check`。
6. 如果设备还在线，服务端调用 `streams:refresh`，拿到新地址后继续录。
7. 录制结束后，服务端把 `output_path` 返回给 OpenClaw。
8. OpenClaw 再告诉商家换下一个位置，直到拍完。

## 本地目录建议

建议第一版先用本地目录，不要急着上对象存储。

```text
/data/captures/
  device-BG4238078/
    session-20260325-120000/
      session.json
      01-interior-wide.mp4
      02-staff-working.mp4
      metadata.json
```

如果暂时没有 `/data` 目录，开发阶段可以先用：

```text
/Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control/artifacts/server-captures/
```

## 最简单的服务器怎么起

如果你现在“不太懂服务器”，第一版不要先买云主机，也不要先上容器。

最简单的做法是：

- 先在你当前这台 Mac 上起一个本地服务
- 用 Python `FastAPI` 做 5 个接口
- OpenClaw 本地调用这个服务
- 等流程跑顺后，再迁移到正式服务器

### 推荐第一版技术栈

- Python 3.11+
- FastAPI
- Uvicorn
- 继续复用当前已有脚本：
  - `scripts/ezviz_cb60_control.py`
  - `scripts/cb60_capture_workflow.py`

### 本地服务最小安装步骤

在项目根目录执行：

```bash
cd /Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install fastapi uvicorn
```

然后准备一个服务目录，例如：

```text
server/
  app.py
  service.py
```

本地启动命令可以先用：

```bash
uvicorn server.app:app --host 127.0.0.1 --port 8090 --reload
```

这样 OpenClaw 可以直接调用：

```text
http://127.0.0.1:8090
```

## 为什么第一版先本地起服务

因为这样最容易排查问题：

- 萤石流断了
- 地址刷新失败
- 文件没有保存
- MP4 转码慢

这些问题在本地都比在远端服务器上容易看清楚。

## 什么时候再迁移到正式服务器

满足下面 3 条后再迁移就比较稳：

- 本地工作流已经能连续跑 3 到 5 次
- 录制、重连、落盘都稳定
- 你已经明确需要多设备或远程运行

## 当前已知边界

- 不能承诺绝对不断流
- 当前接受“短暂断流后自动恢复”
- `CB60` 是 `BatteryCamera`
- 设备在线不代表直播流持续可用
- 直播地址需要准备刷新机制
- 公开 API 目前不能直接切换广角/3x 镜头

## 建议的第一步

如果你现在就要开始做，最推荐的顺序是：

1. 先在本机起一个 FastAPI 服务。
2. 先只实现：
   - `POST /v1/capture-sessions`
   - `POST /v1/capture-sessions/{session_id}/shots/{shot_id}:record`
   - `POST /v1/devices/{device_serial}:health-check`
3. 先把素材稳定保存到本地。
4. 等这条链路跑通，再补 `streams:refresh`。
