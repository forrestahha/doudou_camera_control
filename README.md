# EZVIZ CB60 Control Plugin

这是一个给 OpenClaw 使用的萤石 `CB60` 控制插件。

当前版本已经围绕真实设备做过验证，重点覆盖：

- 左右转动
- 抓拍
- 设备信息 / 状态 / 电量查询
- 直播流管理
- 获取 `HLS / FLV` 播放地址
- 本地拍摄工作流
- OpenClaw 定时拍摄任务管理
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
- `skills/ezviz-cb60-control/scripts/cb60_task_manager.py`
  - OpenClaw 定时任务、商家口令边界、日报汇总、问题排查
- `skills/ezviz-cb60-control/scripts/cb60_battery_stress.py`
  - 常开流耗电 / 重连压测
- `skills/ezviz-cb60-control/scripts/cb60_status_monitor.py`
  - 设备状态轮询，按固定间隔写本地日志 / CSV / 报告

## 环境变量

插件只从环境变量读取凭据：

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

## 首次安装后的必做配置

把插件下载到一台新电脑后，第一步不是直接跑脚本，而是先把萤石凭据写到本机环境里。

推荐做法是在当前用户目录创建：

- `~/.ezviz_cb60_env`

最推荐的方式是直接运行安装向导：

```bash
python3 scripts/ezviz_cb60_control.py setup-env
```

向导会主动询问这些关键信息：

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

如果你要给第二台或第三台摄像头单独建环境文件：

```bash
python3 scripts/ezviz_cb60_control.py setup-env --output ~/.ezviz_cb60_env_cam2
source ~/.ezviz_cb60_env_cam2
```

如果你更喜欢手工方式，也可以自己创建文件：

```bash
cat > ~/.ezviz_cb60_env <<'EOF'
export EZVIZ_APP_KEY='你的_APP_KEY'
export EZVIZ_APP_SECRET='你的_APP_SECRET'
export EZVIZ_ACCESS_TOKEN='你的_ACCESS_TOKEN'
export EZVIZ_DEVICE_SERIAL='你的_DEVICE_SERIAL'
export EZVIZ_VALIDATE_CODE='你的_VALIDATE_CODE'
export EZVIZ_CHANNEL_NO='1'
EOF

chmod 600 ~/.ezviz_cb60_env
source ~/.ezviz_cb60_env
```

建议先检查是否生效：

```bash
echo "$EZVIZ_DEVICE_SERIAL"
python3 scripts/ezviz_cb60_control.py doctor
```

说明：

- 不要把这些值写进代码、测试、README 示例输出或提交记录
- 不建议继续使用 `/tmp/ezviz.env.shared` 作为长期方案，因为它是临时目录，重启或清理后可能消失
- 以后每次开一个新的终端会话，如果没有自动加载，就先执行一次 `source ~/.ezviz_cb60_env`

如果你有多台摄像头，推荐每台设备一个文件，例如：

- `~/.ezviz_cb60_env_cam1`
- `~/.ezviz_cb60_env_cam2`

插件本身仍然按“单次命令只操作一台设备”的方式工作，但现在支持按次切换目标设备：

```bash
python3 scripts/ezviz_cb60_control.py --env-file ~/.ezviz_cb60_env_cam2 doctor
python3 scripts/cb60_capture_workflow.py --env-file ~/.ezviz_cb60_env_cam2 capture-shot --session ./artifacts/workflows/<session>/session.json
python3 scripts/cb60_status_monitor.py --env-file ~/.ezviz_cb60_env_cam2 run --interval-seconds 60 --max-rounds 5
```

这样你在同一个插件里就能切到不同摄像头，不需要改插件代码，也不需要反复手工 `source`。

## 常用命令

在目录

`/Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control/skills/ezviz-cb60-control`

下执行：

```bash
python3 scripts/ezviz_cb60_control.py setup-env
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
python3 scripts/cb60_status_monitor.py run --interval-seconds 60 --max-rounds 5
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
7. 如果设置了 `EZVIZ_MANAGED_STREAM_ID`，工作流会优先复用这条长期 `streamId` 获取最新地址，而不是默认依赖临时取流地址
8. 如果既没有显式传 `--stream-url`，也没有配置长期 `streamId`，工作流会默认按 `protocol=4 + quality=1 + supportH265=1 + type=1` 现取最新 `FLV` 地址
9. 每次执行都会在 session 目录里写 `capture-log.jsonl` 和 `capture-report.md`
10. 录后会自动验片；失败或异常时会自动截一帧，并补失败分析说明

## OpenClaw 视频拍摄任务流程

这层不是直接控制摄像头，而是把“后台定时任务 + 商家有限交互 + 日报输出”收口成一个独立脚本：

- 后台负责下发拍摄任务与每日时间窗口
- OpenClaw 负责在时间窗口内执行拍摄工作流
- 商家只允许通过唤醒词 `龙虾` 执行三类操作：
  - 修改拍摄时间
  - 排查拍摄问题
  - 停止拍摄

推荐目录：

```bash
./artifacts/task-manager/store-a
```

初始化一个每日重复任务：

```bash
python3 scripts/cb60_task_manager.py init-task \
  --task-root ./artifacts/task-manager/store-a \
  --start-time 11:00 \
  --end-time 12:00 \
  --brief '门头, 店内全景, 办公区工作情况'
```

查看当前任务状态：

```bash
python3 scripts/cb60_task_manager.py task-status \
  --task ./artifacts/task-manager/store-a/task.json
```

判断当前是否应该自动执行：

```bash
python3 scripts/cb60_task_manager.py should-run-now \
  --task ./artifacts/task-manager/store-a/task.json
```

后台修改每日拍摄时间：

```bash
python3 scripts/cb60_task_manager.py set-schedule \
  --task ./artifacts/task-manager/store-a/task.json \
  --start-time 11:00 \
  --end-time 12:30
```

商家口令处理：

```bash
python3 scripts/cb60_task_manager.py merchant-command \
  --task ./artifacts/task-manager/store-a/task.json \
  --text '龙虾，帮我改一下拍摄时间 11:00-12:00'
```

```bash
python3 scripts/cb60_task_manager.py merchant-command \
  --task ./artifacts/task-manager/store-a/task.json \
  --text '龙虾，怎么没有拍摄，帮我找找问题'
```

```bash
python3 scripts/cb60_task_manager.py merchant-command \
  --task ./artifacts/task-manager/store-a/task.json \
  --text '龙虾，停止拍摄'
```

说明：

- 未带唤醒词或超出范围的商家请求会被拒绝
- 停止后不会再自动拍摄，直到后台或运维恢复任务
- `merchant-command diagnose` 会综合任务状态、最近执行结果和设备状态输出问题原因

## 拍摄日报与执行日志

OpenClaw 每次完成一个拍摄 session 后，建议立刻把 session 结果记入任务日志：

```bash
python3 scripts/cb60_task_manager.py record-session \
  --task ./artifacts/task-manager/store-a/task.json \
  --session ./artifacts/workflows/<session>/session.json \
  --uploaded-success-count 3 \
  --uploaded-failed-count 0
```

这样任务脚本就能按天汇总：

- 摄像头运行是否正常
- 一天总共拍了多少次
- 一共拍了多少片段
- 多少片段验片通过
- 多少片段上传成功
- 最近一次失败原因是什么

生成日报：

```bash
python3 scripts/cb60_task_manager.py daily-report \
  --task ./artifacts/task-manager/store-a/task.json \
  --status-root ./artifacts/status-monitor/live
```

日报输出会落到：

- `daily-report-YYYY-MM-DD.md`

任务事件日志会写到：

- `task-events.jsonl`

## 推荐的 OpenClaw 调用顺序

推荐把插件接成这条链：

1. 后台先用 `init-task` 或 `set-schedule` 写入每日拍摄窗口
2. OpenClaw 到时间后先调用 `should-run-now`
3. 如果返回可执行，再调用 `cb60_capture_workflow.py`
4. session 完成后调用 `record-session`
5. 状态轮询单独常驻运行
6. 日终或商家追问时，用 `daily-report` 和 `diagnose-task` 输出汇总

## 设备状态轮询

推荐默认值：

- 每 `60` 秒轮询一次
- 只查询设备状态，不取流、不预览
- 本地持续写日志

命令：

```bash
python3 scripts/cb60_status_monitor.py run --interval-seconds 60 --output-root ./artifacts/status-monitor/live
```

输出文件：

- `samples.jsonl`
- `samples.csv`
- `events.jsonl`
- `report.md`

说明：

- 轮询底层走的是 `device-info / device-status / battery`
- 和视频取流相比，这种纯状态查询的耗电可以认为很低，更适合常驻心跳
- 如果你们后面需要设备在线、信号、电量、隐私状态的分钟级心跳，优先用这条链路

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

- `61/61` 通过

运行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```
