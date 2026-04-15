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
- 本地视频和云上最终成片默认都会带拍摄时间戳，格式为 `YYYYMMDD-HHMMSS`
- 云上原始/最终视频会按商家 TOS 前缀自动命名，例如：`store1_jsspa_20260415_101530_original_01.mp4`
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

如果是由 OpenClaw 安装插件并接待用户，推荐先执行：

```bash
python3 scripts/cb60_task_manager.py install-onboarding-message
```

然后把返回的 `message_text` 原样发给用户，先收集安装所需资料；资料配置完成后，再继续问：

```text
你希望这个摄像头在什么时候拍？
```

向导会主动询问这些关键信息：

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

安装向导还会继续追问：

- 是否现在一起配置 `LAS/TOS` 后处理

如果你选择 `yes`，它还会继续询问：

- `LAS_API_KEY`
- `LAS_REGION`
- `TOS_ACCESS_KEY`
- `TOS_SECRET_KEY`
- `TOS_BUCKET`
- `TOS_PREFIX`

这样第一次安装插件时，用户就可以一次性把“拍摄 + LAS/TOS 后处理”都配好。

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
export LAS_API_KEY='你的_LAS_API_KEY'
export LAS_REGION='cn-beijing'
export TOS_ACCESS_KEY='你的_TOS_ACCESS_KEY'
export TOS_SECRET_KEY='你的_TOS_SECRET_KEY'
export TOS_BUCKET='你的_TOS_BUCKET'
export TOS_PREFIX='tos://你的_TOS_BUCKET/openclaw/camera/'
export TOS_ORIGINAL='tos://你的_TOS_BUCKET/openclaw/store1_jsspa_original/'
export TOS_FINAL='tos://你的_TOS_BUCKET/openclaw/store1_jsspa_final/'
export LAS_INPAINT_FIXED_BBOXES='[[0,650,150,970]]'
EOF

chmod 600 ~/.ezviz_cb60_env
source ~/.ezviz_cb60_env
```

建议先检查是否生效：

```bash
echo "$EZVIZ_DEVICE_SERIAL"
python3 scripts/ezviz_cb60_control.py doctor
```

补充说明：
- `LAS_INPAINT_FIXED_BBOXES` 是可选增强项，格式是 `1000x1000` 归一化坐标。
- 当前默认值针对“左下角时间水印”做了加强；如果不同机型位置有偏差，可以按实际情况微调。

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

说明：

- `stream-address` 这里走的是新版直播流地址接口，协议值是：
  - `1=hls`
  - `2=rtmp`
  - `3=flv`
- 为了兼容旧调用，CLI 里如果传了 `4`，也会自动按 `flv` 处理

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
11. 验片通过后，session 会自动生成一条 LAS 后处理流水线状态，固定顺序为：
    - 上传到火山 TOS
    - LAS 高光剪辑
    - LAS 去水印
    - LAS 变高清
12. 由于当前仓库还没有接入你的 TOS 上传访问，这条 LAS 流水线会先标记成 `pending_config`，但状态和后续顺序已经写进 `session.json`、`capture-log.jsonl`、`capture-report.md`

### LAS 后处理说明

OpenClaw 录完本地视频后的目标链路已经固定为：

1. 先把本地视频传到火山 TOS
2. 调用 `byted-las-video-edit` 做高光剪辑
3. 调用 `byted-las-video-inpaint` 做去水印
4. 调用 `byted-las-video-resize` 做变高清

当前版本先保留这条编排逻辑，不会擅自发起云端上传。原因是还缺少你后面要提供的 TOS 访问配置。

所以你现在会在每条拍摄结果里看到一段 `postprocess` 状态：

- `accepted` 片段：LAS 流水线状态会是 `pending_config`
- `abnormal/failed` 片段：LAS 流水线状态会是 `skipped_capture_not_accepted`

这保证了：

- 本地拍摄主流程不受影响
- OpenClaw 已经知道后面该按什么顺序调 LAS
- 等你补了 TOS 访问后，不需要再重构录制工作流

## OpenClaw 视频拍摄任务流程

这层不是直接控制摄像头，而是把“后台定时任务 + 商家有限交互 + 日报输出”收口成一个独立脚本：

- 后台负责下发拍摄任务与每日时间窗口
- OpenClaw 负责在时间窗口内执行拍摄工作流
- 首次开机时，商家只需要回答一个问题：`你希望这个摄像头在什么时候拍？`
- 商家只允许通过唤醒词 `龙虾` 执行三类操作：
  - 修改拍摄时间
  - 排查拍摄问题
  - 停止拍摄

另外新增一条前置规则：

- 在下一次拍摄开始前 `1` 小时，OpenClaw 应自动执行一次电量检查
- 如果电量低于 `85%`，则输出提醒商家充电

## 固定命令规则

这套插件默认是“**首次说一次，后面每天自动跑**”的模式，不是一次性手动脚本。

### 1. 首次安装后的唯一必问句

OpenClaw 第一次只需要问商家一句：

- `你希望这个摄像头在什么时候拍？`

商家给出时间段后，例如：

- `11:00-12:00`
- `11点到12点`

就立刻调用：

```bash
python3 scripts/cb60_task_manager.py first-boot-setup \
  --task-root ./artifacts/task-manager/store-a \
  --time-window-text '11:00-12:00'
```

这个动作会直接创建一个：

- 每日重复
- 持续生效
- 直到商家说“停止拍摄”或后台停用

的定时任务。

### 2. 商家口令边界

商家现在只能说这四类命令：

- 修改拍摄时间
  - `龙虾，帮我改一下拍摄时间 11:00-12:00`
- 临时拍摄
  - `龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00`
- 排查拍摄问题
  - `龙虾，怎么没有拍摄，帮我找找问题`
- 停止拍摄
  - `龙虾，停止拍摄`

除此之外，插件默认不接受其他商家侧口令。

说明：

- 临时拍摄模式不会覆盖原来的每日定时任务。
- 每日定时任务照常保留，临时拍摄只是在当前这次额外生效。
- 如果商家只说“帮我拍视频”，OpenClaw 应继续追问两个参数：
  - 每隔多久拍一条
  - 要拍到几点
- 单条视频时长默认仍按插件当前默认值执行，除非后续工作流另行指定。

### 3. 每日自动执行顺序

OpenClaw 每天都按这条固定顺序跑：

1. 开拍前 `60` 分钟运行 `battery-precheck`
2. 如果电量 `< 85%`，提醒商家充电
3. 到达时间窗口后运行 `should-run-now`
4. 如果结果是 `true`，开始拍摄工作流
5. 拍完调用 `record-session`
6. 按天调用 `daily-report`

### 3.1 临时拍摄模式

如果商家临时说：

- `龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00`

插件会创建一个“临时拍摄窗口”：

- 立即生效
- 到商家指定的结束时间自动结束
- 不修改原来的每日定时窗口

OpenClaw 在运行 `should-run-now` 时，现在要同时判断两件事：

1. 当前是否落在每日定时窗口内
2. 当前是否落在临时拍摄窗口内

只要任意一个命中，就应该执行拍摄。

### 4. 视频工作流固定规则

如果用户没有特别强调“只测摄像头”，默认按完整链路执行：

1. 录视频
2. 转竖屏 MP4
3. 自动验片
4. 失败自动截帧和分析
5. 上传 TOS
6. 调 LAS 高光剪辑
7. 调 LAS 去水印
8. 调 LAS 变高清

默认取流规则固定为：

- `protocol=4`
- `quality=1`
- `supportH265=1`
- `type=1`

默认 LAS 规则固定为：

- 高光剪辑：
  - 只保留营业高光动态画面
  - 剔除静态空镜、无人物、无动作、无画面变化的无效内容
  - 重点保留上菜全过程、后厨食材处理、前台接待或操作、人员走动互动、餐具摆放等营业相关动态
- 去水印：
  - 默认去掉全部可见水印
  - 默认使用精细检测
- 变高清：
  - 默认输出 2K 规格竖屏视频

### 5. 一键查看这套规则

你也可以直接让插件输出这份固定契约：

```bash
python3 scripts/cb60_task_manager.py workflow-spec
```

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

如果你想按“首次开机只问一个时间段”的方式接入，推荐直接用：

```bash
python3 scripts/cb60_task_manager.py first-boot-setup \
  --task-root ./artifacts/task-manager/store-a \
  --time-window-text '11:00-12:00'
```

这个入口默认就会把商家提供的时间窗口写成“每日重复定时任务”。

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

在下次拍摄开始前 1 小时，执行电量预检查：

```bash
python3 scripts/cb60_task_manager.py battery-precheck \
  --task ./artifacts/task-manager/store-a/task.json
```

返回里会明确告诉你：

- 现在是不是该查电量
- 下次拍摄开始时间
- 当前电量
- 是否需要提醒商家充电

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
  --text '龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00'
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
