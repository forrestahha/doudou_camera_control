# EZVIZ CB60 Control Plugin

这是一个给 OpenClaw 使用的萤石 `CB60` 控制插件。

当前版本已经围绕真实设备做过验证，重点覆盖：

- 左右转动
- 抓拍
- 设备信息 / 状态 / 电量查询
- 直播流管理
- 获取 `HLS / FLV` 播放地址
- 本地拍摄工作流
- 录制后自动转旋转版 `MP4`

## 当前已验证能力

真实设备验证已确认：

- `PTZ left/right` 可用
- 抓拍可用
- 通过直播流管理创建 `streamId` 后，可稳定获取新的 `FLV` 和 `HLS` 地址
- 通过新的 `FLV` 地址可以连续录制，并落盘为本地文件
- 录制结果可以转成通用 `MP4`
- 电量、设备基础信息、设备状态查询可用

## 当前边界

- 设备主码流当前为 `H.265`
- 设备编码格式查询可用，但当前设备返回 `60020`，不支持通过该接口切到 `H.264`
- `REST zoom` 在这台设备上不可用
- 公开 API 没有确认可直接切换广角 / 3x 镜头
- 对讲仍停留在 SDK 边界，插件未直接实现

## 目录结构

- `openclaw.plugin.json`
  - OpenClaw 插件入口
- `skills/ezviz-cb60-control/SKILL.md`
  - 插件主说明
- `skills/ezviz-cb60-control/scripts/ezviz_cb60_control.py`
  - 设备控制、状态查询、直播流管理
- `skills/ezviz-cb60-control/scripts/cb60_capture_workflow.py`
  - 本地拍摄工作流，录制后自动转 `MP4`
- `skills/ezviz-cb60-control/scripts/cb60_battery_stress.py`
  - 常开流耗电 / 重连压测

## 环境变量

插件只从环境变量读取凭据：

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

## 常用命令

在目录

`/Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control/skills/ezviz-cb60-control`

下执行：

```bash
python3 scripts/ezviz_cb60_control.py doctor
python3 scripts/ezviz_cb60_control.py capabilities
python3 scripts/ezviz_cb60_control.py device-info
python3 scripts/ezviz_cb60_control.py device-status
python3 scripts/ezviz_cb60_control.py battery
python3 scripts/ezviz_cb60_control.py video-encode-get --stream-type 1
python3 scripts/ezviz_cb60_control.py stream-create --start-time '2026-03-25 19:15:55' --end-time '2026-03-25 21:15:55'
python3 scripts/ezviz_cb60_control.py stream-address --stream-id <stream_id> --protocol 3 --quality 1 --support-h265 1
python3 scripts/ezviz_cb60_control.py snapshot --output /tmp/cb60.jpg
python3 scripts/ezviz_cb60_control.py ptz left --duration 1.0
```

## 本地拍摄工作流

```bash
python3 scripts/cb60_capture_workflow.py init-session --brief '门头, 店内全景, 商品近景'
python3 scripts/cb60_capture_workflow.py next-shot --session ./artifacts/workflows/<session>/session.json
python3 scripts/cb60_capture_workflow.py capture-shot --session ./artifacts/workflows/<session>/session.json --stream-url '<flv-or-hls-url>'
```

现在 `capture-shot` 会：

1. 录制原始视频
2. 如果本机存在 `ffmpeg`，自动转成旋转后的 `.mp4`
3. 默认输出模式是整体顺时针 `90°`，适合把横屏素材直接变成竖屏
4. 也支持 `flip180` 这种整体翻转模式
5. 原始文件保存在 `raw_output_path`
6. 如果同时有 `FLV` 和 `HLS`，优先使用 `FLV`

## 建议的正式链路

推荐分工：

- `OpenClaw`
  - 负责工作流、镜头规划、商家提示
- `采集端`
  - 负责创建直播流、获取播放地址、录制、重连、落盘

更详细的说明见：

- [minimal-server-architecture.md](/Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control/skills/ezviz-cb60-control/references/minimal-server-architecture.md)

## 测试状态

当前本地测试状态：

- `33/33` 通过

运行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```
