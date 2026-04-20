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

## OpenClaw 需要理解的核心规则

这套插件不是“只会拍视频”的单点脚本，而是一个完整的商家视频任务插件。  
OpenClaw 接入时，应该把它理解成下面这 4 层：

1. **设备控制层**
- 控制摄像头左右转动、抓拍、查状态、查电量、拿直播地址

2. **拍摄工作流层**
- 录视频
- 自动转竖屏 `MP4`
- 自动验片
- 失败自动截帧和分析

3. **任务管理层**
- 每日重复定时任务
- 商家有限口令边界
- 临时拍摄模式
- 开拍前 1 小时电量提醒
- 日报和问题排查

4. **云上后处理层**
- 上传火山 TOS
- LAS 高光剪辑
- LAS 去水印
- LAS 变高清

换句话说，如果用户没有特别强调“只测摄像头”，OpenClaw 默认应该按**完整链路**运行，而不是只停在本地录制。

## 环境变量与安装配置

插件所有敏感配置都从环境变量读取，不会写进代码。

### 萤石拍摄必需项

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

`EZVIZ_ACCESS_TOKEN` 有 7 天有效期。插件在调用萤石接口时如果遇到 `10002: accessToken过期或参数异常`，会自动使用 `EZVIZ_APP_KEY` + `EZVIZ_APP_SECRET` 刷新一次 token，并在使用 `--env-file` 时写回同一个环境文件。OpenClaw 不需要现场写脚本刷新 token。

运行时 OpenClaw 不应该热修插件源码。商家现场环境只能修改环境变量、任务状态、OpenClaw 运行配置等非源码配置。如果发现 `scripts/`、`skills/`、`tests/`、`README.md`、`SOUL.md` 或 `openclaw.plugin.json` 的代码/文档问题，应记录错误并提示更新 GitHub 最新插件，而不是在 `/root/.openclaw/workspace/...` 里直接改文件。

如果要走完整的 TOS/LAS 链路，请在运行环境镜像里预装 Python 包 `tos`。不要让 OpenClaw 在现场猜包名并临时 `pip install`。推荐先运行：

```bash
python3 scripts/ezviz_cb60_control.py doctor
python3 scripts/ezviz_cb60_control.py tos-preflight
```

用它来区分：
- `tos` SDK 没装
- TOS 配置缺失
- TOS endpoint DNS 解析失败
- Bucket / 鉴权 / 网络访问失败

### 火山云 LAS/TOS 完整后处理必需项

- `LAS_API_KEY`
- `LAS_REGION`
- `TOS_ACCESS_KEY`
- `TOS_SECRET_KEY`
- `TOS_ORIGINAL`
- `TOS_FINAL`

### 可选增强项

- `LAS_INPAINT_FIXED_BBOXES`
  - 1000x1000 归一化坐标
  - 默认已经针对“左下角时间水印”强化
- `EZVIZ_MANAGED_STREAM_ID`
  - 如果你们要长期复用某条直播流

### 首次安装后的正确顺序

把插件下载到一台新电脑后，OpenClaw 不应该直接开始问拍摄时间。  
正确顺序是：

1. 先调用：

```bash
python3 scripts/cb60_task_manager.py install-onboarding-message
```

2. 把返回的 `message_text` 原样发给用户，让用户先提供：
- 萤石凭据
- LAS/TOS 凭据
- 商家自己的两个 TOS 文件夹

3. 再执行安装向导：

```bash
python3 scripts/ezviz_cb60_control.py setup-env
```

4. 环境配置完成后，OpenClaw 再继续问唯一那句：

```text
你希望这个摄像头在什么时候拍？
```

### 安装向导会主动问什么

向导会先问萤石拍摄必需项，然后继续追问完整工作流所需的火山云字段：

- `LAS_API_KEY`
- `LAS_REGION`
- `TOS_ACCESS_KEY`
- `TOS_SECRET_KEY`
- `TOS_ORIGINAL`
- `TOS_FINAL`

也就是说，**安装后 OpenClaw 不需要自己发明第二套问答逻辑**；完整插件默认就要求这些字段全部到位。只要：

- 先发资料采集说明
- 再跑 `setup-env`

就可以了。

### 推荐环境文件

推荐把配置写到：

- `~/.ezviz_cb60_env`

如果有第二台或第三台设备，继续按这个模式建：

- `~/.ezviz_cb60_env_cam2`
- `~/.ezviz_cb60_env_cam3`

然后通过 `--env-file` 切换摄像头，而不是改插件代码。

例如：

```bash
python3 scripts/ezviz_cb60_control.py --env-file ~/.ezviz_cb60_env_cam2 doctor
python3 scripts/cb60_capture_workflow.py --env-file ~/.ezviz_cb60_env_cam2 capture-shot --session ./artifacts/workflows/<session>/session.json
python3 scripts/cb60_status_monitor.py --env-file ~/.ezviz_cb60_env_cam2 run --interval-seconds 60 --max-rounds 5
```

### 手工环境文件模板

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
export TOS_ORIGINAL='tos://doudou-video/openclaw/store1_jsspa_original/'
export TOS_FINAL='tos://doudou-video/openclaw/store1_jsspa_final/'

export LAS_INPAINT_FIXED_BBOXES='[[0,650,150,970]]'
EOF

chmod 600 ~/.ezviz_cb60_env
source ~/.ezviz_cb60_env
```

验证配置：

```bash
python3 scripts/ezviz_cb60_control.py doctor
```

## 商家 TOS 目录规则与云上命名

商家云上目录不是随便填的，插件默认按这套规则工作：

- 每个商家在 `openclaw/` 下有两个目录
- 一个放原始视频
- 一个放最终成片

例如：

- `tos://doudou-video/openclaw/store1_jsspa_original/`
- `tos://doudou-video/openclaw/store1_jsspa_final/`

插件会自动从目录名里提取商家标识：

- `store1_jsspa`

然后按固定格式命名云上文件：

- 原始视频：
  - `store1_jsspa_YYYYMMDD_HHMMSS_original_01.mp4`
- 最终成片：
  - `store1_jsspa_YYYYMMDD_HHMMSS_final_01.mp4`

例如：

- `store1_jsspa_20260415_123306_original_01.mp4`
- `store1_jsspa_20260415_123306_final_01.mp4`

这个命名已经写死在插件里，OpenClaw 不需要自己拼名字。

## OpenClaw 标准接入流程

推荐把插件接成这条固定链路：

1. 插件安装完成
2. 调 `install-onboarding-message`
3. 把 `message_text` 发给用户，收齐配置
4. 跑 `setup-env`
5. 再问：
   - `你希望这个摄像头在什么时候拍？`
6. 用 `first-boot-setup` 创建每日重复任务
7. 立刻根据 `scheduler_spec` 给 OpenClaw 创建周期检查定时任务
8. 日常由 OpenClaw 周期执行：
   - `battery-precheck`
   - `should-run-now`
   - `capture workflow`
   - `record-session`
   - `daily-report`

### 首次安装到自动拍摄的最小闭环

如果你要让一台新机器上的 OpenClaw “装完就能按时自动拍”，最小闭环就是下面这套顺序：

1. 安装插件并进入 skill 目录
2. 调 `install-onboarding-message`，把返回的资料采集说明发给用户
3. 跑 `setup-env` 写入萤石和 LAS/TOS 环境变量
4. 跑 `doctor` 和 `tos-preflight`，确认运行环境没缺依赖、没缺 bucket/网络
5. 问商家唯一那句：`你希望这个摄像头在什么时候拍？`
6. 调 `first-boot-setup`
7. 立刻调 `scheduler-spec`
8. OpenClaw 根据 `scheduler-spec` 创建每 10 分钟一次的周期任务
9. 创建完成后回写 `scheduler-installed`
10. 之后才算真正进入“每天自动拍摄”

也就是说，**只写 `task.json` 还不够**，必须真的把 OpenClaw 周期调度器建起来。

推荐直接照着执行：

```bash
python3 scripts/cb60_task_manager.py first-boot-setup \
  --task-root ./artifacts/task-manager/store-a \
  --time-window-text '11:00-12:00'

python3 scripts/cb60_task_manager.py scheduler-spec \
  --task ./artifacts/task-manager/store-a/task.json

python3 scripts/cb60_task_manager.py scheduler-installed \
  --task ./artifacts/task-manager/store-a/task.json \
  --automation-name doudou_camera_shot_check_store_a \
  --delivery-channel main-session-channel
```

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

## OpenClaw 推荐默认拍摄策略

OpenClaw 不应该在现场临时猜这些参数，也不应该边跑边改源码。插件当前已经把默认拍摄策略固化好了：

### 1. 默认优先链路

- 优先尝试 `FLV`
- 默认参数：
  - `protocol=4`
  - `quality=1`
  - `supportH265=0`
  - `type=1`

这样做的目的，是先优先请求 **H264 兼容链路**，尽量避免直接拿到萤石的 H265 占位图。

### 2. `source` 参数自动兼容

不同萤石接口环境对 `source` 的要求不一致，插件已经做了自适应处理：

- 如果接口返回 `source为空`
  - 插件会自动补 `source=1` 再试
- 如果接口返回 `source格式非法`
  - 插件会自动去掉 `source` 再试

OpenClaw 不需要现场手动判断到底该不该传 `source`。

### 3. 首次拍摄异常时的自动救援

如果第一次拍出来的是下面这几类结果：

- 极低分辨率
- 明显异常短片
- 未通过验片
- 很像拿到了占位图或兼容子流

插件会自动再试一次：

- `H265 + HLS`
- 即：
  - `protocol=1`
  - `supportH265=1`

只有第二次验片通过，才会把第二次结果作为最终结果保留下来。  
所以 OpenClaw 现在不应该再自己去做“先改环境变量、再手动重建流、再手动切协议”这类现场判断。

### 4. 最终交付格式

不管上游实际拉到的是 H264 还是 H265，插件的最终目标都是：

- 尽可能拿到完整可用的原始画面
- 再本地转码成竖屏 `MP4`
- 最终交付给业务侧的是更通用的 `H264 MP4`

所以“最终输出要 H264”和“上游必要时允许 H265 主码流救援”并不冲突。

## 视频拍摄工作流

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
8. 如果既没有显式传 `--stream-url`，也没有配置长期 `streamId`，工作流会默认按 `protocol=4 + quality=1 + supportH265=0 + type=1` 现取最新 `FLV` 地址，优先请求 H264 兼容链路；`source` 会自适应重试：接口报“source为空”时补 `1`，接口报“source格式非法”时自动去掉
9. 如果第一次录到的是低分辨率/异常短片/未通过验片，工作流会自动再尝试一次 `H265 + HLS` 主码流；只要第二次通过验片，就自动采用第二次结果，不需要 OpenClaw 现场改参数
10. 每次执行都会在 session 目录里写 `capture-log.jsonl` 和 `capture-report.md`
11. 录后会自动验片；失败或异常时会自动截一帧，并补失败分析说明
12. 验片通过后，会自动进入 LAS 后处理流水线，固定顺序为：
    - 上传到火山 TOS
    - LAS 高光剪辑
    - LAS 去水印
    - LAS 变高清
13. 单次执行有墙钟上限：默认普通拍摄最多 `180` 秒，含 LAS 全流程最多 `1800` 秒；超时会写入 `capture_timed_out` 日志并停止
14. 本地文件和云上文件都会带拍摄时间戳

### 插件已经自动处理的常见拍摄异常

OpenClaw 看到下面这些情况时，不应该第一反应是自己改代码或改一堆环境变量；插件已经内置了处理逻辑：

1. `10001: source为空`
- 插件会自动补 `source=1` 重试

2. `10001: source格式非法`
- 插件会自动去掉 `source` 重试

3. `FLV` 录制超时 / 连不上
- 插件原本就会在合适场景下回退到 `HLS`

4. 第一次拍到的流分辨率过低、时长过短、验片失败
- 插件会自动再尝试一次 `H265 + HLS`

5. 运行环境缺少 TOS Python SDK
- `tos-preflight` 会明确告诉你缺的是 Python 包 `tos`
- 不要让 OpenClaw 在现场猜包名并尝试 `pip install`

### 仍需要人工介入的情况

下面这些不属于插件现场自动修复范围，应该记日志并提示排查环境或设备：

- 萤石控制面 API 本身不可用
- 当前运行环境完全拉不到媒体流
- TOS endpoint DNS 或 bucket 鉴权失败
- 设备电量过低、设备离线
- LAS 技能或运行依赖压根不存在

可通过环境变量调整上限：

```bash
export CB60_CAPTURE_WALL_TIMEOUT_SECONDS='180'
export CB60_CAPTURE_WITH_LAS_WALL_TIMEOUT_SECONDS='1800'
```

### LAS 后处理固定策略

只要配置齐了 LAS/TOS，插件默认就会在每条验片通过的视频后自动执行：

1. **上传 TOS**
2. **LAS 高光剪辑**
3. **LAS 去水印**
4. **LAS 变高清**

默认策略已经固化：

#### 高光剪辑
- 识别营业高光动态画面
- 剔除静态空镜、无人物、无动作、无画面变化的无效内容
- 重点保留：
  - 上菜全过程
  - 后厨食材处理
  - 前台接待 / 操作
  - 人员走动互动
  - 餐具摆放等营业相关动态

#### 去水印
- 目标：全部可见水印
- 默认开启精细检测
- 对左下角时间水印增加固定框强化
- 当前默认固定框：

```bash
export LAS_INPAINT_FIXED_BBOXES='[[0,650,150,970]]'
```

#### 变高清
- 默认输出 **2K 规格竖屏视频**

### 本地与云上产物

每次拍摄后通常会有这些文件：

本地：
- 原始流文件 `.flv` 或 `.ts`
- 旋转后的 `.mp4`
- `capture-log.jsonl`
- `capture-report.md`
- 失败时的截帧图片

云上：
- 原始上传视频
- LAS 高光剪辑中间视频
- LAS 去水印中间视频
- 最终高清成片

## OpenClaw 视频拍摄任务流程

这层不是直接控制摄像头，而是把“后台定时任务 + 商家有限交互 + 日报输出”收口成一个独立脚本：

- 后台负责下发拍摄任务与每日时间窗口
- OpenClaw 负责在时间窗口内执行拍摄工作流
- 首次开机时，商家只需要回答一个问题：`你希望这个摄像头在什么时候拍？`
- 商家只允许通过唤醒词 `龙虾` 执行四类操作：
  - 修改拍摄时间
  - 临时拍摄
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

这里“到点自动拍”的前提是：

- `first-boot-setup` 已执行
- `scheduler-spec` 已读取
- OpenClaw 周期任务已经真的创建完成
- `scheduler-installed` 已回写

如果只写了任务配置，但没有真的创建周期检查任务，那么到时间也不会自动拍摄。

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
- `supportH265=0`
- `source` 自适应重试：先按现有配置请求，必要时补 `1` 或自动去掉
- 如果第一次低质量 / 异常短片 / 验片失败：自动重试一次 `H265 + HLS`
- `type=1`

默认 LAS 规则固定为：

- 高光剪辑：
  - 只保留营业高光动态画面
  - 剔除静态空镜、无人物、无动作、无画面变化的无效内容
  - 重点保留上菜全过程、后厨食材处理、前台接待或操作、人员走动互动、餐具摆放等营业相关动态
- 去水印：
  - 默认去掉全部可见水印
  - 默认使用精细检测
  - 默认强化左下角时间水印区域
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
同时它还会返回一份 `scheduler_spec`，OpenClaw 必须立刻按这份配置创建周期检查任务；只写 `task.json` 不创建调度器，到时间不会自动拍摄。

查看 OpenClaw 必须创建的周期任务配置：

```bash
python3 scripts/cb60_task_manager.py scheduler-spec \
  --task ./artifacts/task-manager/store-a/task.json
```

在 OpenClaw 创建完周期任务后，建议回写一条确认记录：

```bash
python3 scripts/cb60_task_manager.py scheduler-installed \
  --task ./artifacts/task-manager/store-a/task.json \
  --automation-name doudou_camera_shot_check \
  --delivery-channel main-session-channel
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

1. 后台先用 `init-task` 或 `first-boot-setup` 写入每日拍摄窗口
2. 立即读取 `scheduler-spec`，给 OpenClaw 创建每 10 分钟一次的周期检查任务
3. 周期任务先调用 `battery-precheck`
4. 再调用 `should-run-now`
5. 如果返回可执行，再调用 `cb60_capture_workflow.py`
6. session 完成后调用 `record-session`
7. 状态轮询单独常驻运行
8. 日终或商家追问时，用 `daily-report` 和 `diagnose-task` 输出汇总

## OpenClaw 调用时的判断规则

为了让 OpenClaw 不误解这套插件，建议固定按下面这些规则理解：

1. **默认是完整链路**
- 如果没有特别强调“只拍摄不走后处理”，就默认：
  - 拍摄
  - 本地 MP4
  - 上传 TOS
  - LAS 高光剪辑
  - LAS 去水印
  - LAS 变高清

2. **每日任务是长期任务**
- 首次说一次拍摄时间后，后面每天都重复执行
- 除非商家明确说：
  - `龙虾，停止拍摄`

3. **临时拍摄不会覆盖每日任务**
- 商家临时要求“现在拍”，只是额外加一个临时拍摄窗口
- 原来的每日任务照常存在

4. **多摄像头通过 env-file 切换**
- 不是一个任务里同时操作多台
- 而是一次命令只操作当前环境中的一台设备

5. **云上文件命名不用 OpenClaw 自己拼**
- 插件会自动按商家 TOS 目录推导商家名
- 自动生成 `original/final` 文件名

6. **日志和报告是默认产物**
- 本地拍摄日志
- 本地拍摄报告
- 任务事件日志
- 每日报告
- 状态轮询日志

OpenClaw 不需要自己再重复造一套日志结构。

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

## 当前测试状态

当前最近一轮本地相关回归：

```bash
python3 -m unittest tests.test_cb60_task_manager tests.test_cb60_capture_workflow tests.test_cb60_interval_capture_test tests.test_ezviz_cb60_control
```

结果：

- `77/77` 通过
