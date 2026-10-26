# Arithmetic-Mahjong-Simulator

算术麻将的 Python 牌局后端、本地真人房间及人机牌桌。普通模式支持点胡、自摸、抢杠、流局结算和手动下一局。已提供 Bot、Arena 与实验训练，以及独立的本地 RL 人机试玩入口；血战暂未实现。

## 本地人机试玩

安装 Python 3.13 后，在仓库根目录打开 PowerShell。首次运行先创建运行环境并安装依赖，无需 PyTorch、CUDA 或独立显卡：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r multiplayer/requirements-bot.txt
.\.venv\Scripts\python.exe -m multiplayer.bot_play --port 8772 --model .data/models/rl-3h/snapshots/ppo-0068.npz
```

上述命令显式选择完成训练的第 68 批，不依赖作者机器上的 RL 缓存。发布时需一同提供下面四份推理导出；其他训练数据、检查点和评测文件均不是游玩依赖：

```text
.data/models/p4-v1/policy.npz
.data/models/rl-3h/snapshots/ppo-0068.npz
.data/models/cfr-mahjong/policy.json.gz
.data/models/cfr-abstract-v1/policy.json.gz
```

没有模型时也可运行 `.\.venv\Scripts\python.exe -m multiplayer.bot_play --port 8772 --no-models`，使用基线、数学、搜索和启发式风格策略。

访问 [8772 人机试玩](http://127.0.0.1:8772)，创建房间后默认安排你和三家 Bot。房主设置按类别提供全部 25 个配置：基线、数学、搜索、启发式四风格、NN 四风格、RL 四风格、精确/抽象 CFR 各四风格。缺少模型的项禁用，不影响非学习策略开局。拖牌、分组、吃碰杠胡过仍用原操作。默认计时为 t1/t2/t4 各 5 秒、t3 30 秒，可在首局开局前调整或关闭。

可与 RL 长训同时运行：默认重启沿用私有缓存中的冻结 RL，当前已更新为训练器开发验证选中的第 68 轮（最后完成的是第 71 轮）；不读取训练正在更新的别名或检查点，不自动更新。NN 和两类 CFR 各有独立冻结模型。明确传 `--model <编号快照>` 才更换 RL；传训练目录会选最新完成批，不等于验证选中批。`--no-models` 可完全不读模型。只用一个低优先级 CPU 推理进程、不导入 Torch/CUDA，不修改训练源码或输出，但同机仍会有少量资源竞争。当前环境已安装所需依赖，新环境可装 `multiplayer/requirements-bot.txt`，不要改训练虚拟环境。

只监听本机，一次一个房间，关服重置；纯真人 8771 保持独立。详细限制、超时保底和验证见 [人机试玩设计](design_docs/bot_play_design.md)。

## 电脑开服模式

按最新目标新增独立入口：**每次启动从空白房间开始，关服后不恢复**。当前本机预览为 [8771](http://127.0.0.1:8771)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r multiplayer/requirements.txt
.\.venv\Scripts\python.exe -m multiplayer.web --port 8771
# 或 .\multiplayer\start.ps1 -Port 8771，Ctrl+C 关服
```

项目虚拟环境可先用 Python 3.13 的 `python -m venv .venv` 创建。新入口使用 FastAPI/Uvicorn 单 worker，仍只绑定本机；状态继续轮询，独立 WebSocket 负责连接存活。原窗口刷新/多标签页可交接；非房主参赛者确认掉线后暂停全桌，默认每席每局累计 300 秒等待原凭证返回，期间所有操作时间冻结。逾期只过牌、摸什么打什么，本局回来仍托管，下一局可恢复真人。房主个人失联关闭本房，普通观众离线不暂停；疑似全服同时断链时先保护暂停，避免批量误删。

新入口不读写或删除旧 SQLite 存档；进程重新启动后旧身份和操作失效，原页面自动回大厅。**Access 身份绑定、Tunnel 和域名还未接入，当前不要发布到外网。** `--public-origin` 只是精确 HTTPS 来源的预留配置，不是身份保护。完整边界与测试见 [电脑开服设计](design_docs/hosting_design.md)。

旧测试入口已于 2026-10-26 移除，目前只提供以上两个入口。旧 SQLite 文件保留，但当前服务不载入。

## 实现状态

- ✅ 四人发牌、摸牌、按实体牌出牌、手牌排序和自定义分组。
- ✅ t1 响应裁决、t2 吃法选择、t3 暗杠/加杠/自摸及超时处理。
- ✅ 仅加杠可抢的 t4，全部过恢复原 t3 剩余时间；只有合格玩家能操作，倒计时只对本人可见。
- ✅ 可替换的 RuleAdapter 和普通模式 NormalScoringAdapter。
- ✅ 最高番胡法、番种明细、赔付、分数变化和 RoundResult。
- ✅ 一炮多响开关、累计分数、手动开始下一局和庄家固定轮换。
- ✅ 整理与出牌请求支持旧 t3 窗口校验，非法跨组移动不会产生部分修改。
- ✅ 完整 GameState 私有快照及 SQLite 原子存储能力；当前开服入口使用内存状态，关服重置。
- ✅ 本地多人创建/密码加入、十人成员、房主选座开局、四个真人视角、公共/上帝观战、局间换人及座位分数继承。
- ✅ 本地多人结算后全员点击牌山查看完整顺序；独立的赛后查看、安排下一局和确认开局阶段。
- ✅ 独立 ASGI 开服入口每次重置；连接交接、全桌暂停、300 秒宽限、默认摸切、房主个人离线关房与疑似全服断链保护。
- ✅ 独立 Bot P0/P1：玩家观察、完整候选、控制权校验、三个基线及支持计算预算/超时保底的四席 Arena。
- ✅ Bot P2 首版：预算内听牌分析、未见牌统计、组形启发式、吃碰杠比较及逐动作评分说明。
- ✅ Bot P3 实验版本：库存约束的距离/进张、固定待牌概率、可见库存采样及 t3 出牌截断搜索。
- ✅ Bot P3.5 初级风格：速胡、做大、稳守及可调牵制，共用分析、计算预算和候选说明。
- ✅ Bot P4 模仿首版：独立 CUDA 训练、带风格候选网络、CPU 推理、冻结快照与同预算降级。
- ✅ Bot PPO 实验：风格效用、可见信息 critic、旧策略池、精确行为概率、恢复与独立 RL 模型。
- ✅ 独立本地全策略人机试玩：25 个配置、NN/RL/CFR 独立冻结模型、默认不更新 RL、单 CPU 进程与预算保底，保留真人命令路径。
- ❌ 完整搜索/CFR Bot、血战、Access 身份绑定/跨设备找回、Tunnel/公网部署及逐步回放播放器。

## Bot 离线 Arena

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m arena --games 4 --bots math tsumogiri random win-random --output .data/arena/report.json
```

默认快速跑局，四席轮换，每次 bot 计算上限 500 ms；`--mode timed` 使用实际 t1～t4，`--compute-ms` 设置计算预算。包含摸切、随机、先胡再随机三个基线，输出筹码收益、耗时与超时/保底统计。独立进程超时后回收，该席本局使用保底，下一局重新准备策略。当前入口不接管真人桌。

`math` 是 P2 数学策略：复用规则黑箱求听牌与付款，结合未见张数和多胡型组形评分，在预算不足时返回当前最佳候选。它也比较吃碰杠，但尚无完整期望收益或校准的防守模型。加 `--trace .data/arena/trace.jsonl` 可查看候选评分、待牌和分析完成状态；详细初测见 [Bot 设计与评测记录](design_docs/bot_design.md)。

将 `--bots` 改为 `search math win-random random` 可试用 P3。`search` 在相同计算上限内，对 P2 的部分出牌候选进行距离/进张分析和成批假想牌局模拟；预算不足时保留当前可用选择。当前仅搜索 t3 出牌，属于有界启发式实验，尚无完整收益求解或行为后验。P2 保留为独立策略供对照。

将 `--bots` 改为 `fast big defensive math` 可试用 P3.5 的速胡、做大和稳守风格；`blocking` 是稳守加高牵制的预设。这些初级策略不需要训练，所有风格接受合法胡牌，目标是有可辨认的打法。风险/喂牌指标尚未校准，真人 UI 接入和实际陪玩体验仍待验证；使用方法见 [Arena 使用说明](arena/README.md)。

自定义 `Bot.act(observation, legal_actions)`、有限时示例和私有轨迹记录见 [Arena 使用说明](arena/README.md)。

P4 已在本机 RTX 3060 上训练，可用 `.\.venv-bot\Scripts\python.exe -m arena --games 4 --bots nn-fast nn-big nn-defensive nn-blocking` 试跑。模型位于 `.data/models/p4-v1/policy.npz`；缺少模型时会明确降级为对应初级风格。训练使用 GPU，对局使用小型 NumPy CPU 模型并遵守原有计算上限。模仿版安装、重训与恢复见 [训练说明](training/README.md)。

PPO 实验使用 `rl-fast rl-big rl-defensive rl-blocking`，默认模型为 `.data/models/rl-v1/policy.npz`；原 `nn-*` 保留作对照。新增 `python -m training.rl_long`，可自行运行四风格、共享层解冻的 10,240 局训练并断点续训，输出另存 `rl-long/`，通过 Arena `--models` 指定。命令、奖励与恢复见 [RL 说明](training/RL.md)。尚未证明比模仿版更强；独立本地真人试玩见本文开头，正式公网接入及完整体验验收仍未完成。

## 测试

使用 Python 3.13，并在仓库根目录运行：

联网入口新增的接口测试需要项目 `.venv` 安装上述运行依赖；使用 `.\.venv\Scripts\python.exe -m pytest -q` 可一起运行（该环境需另装 pytest）。未安装 FastAPI 的原环境会跳过联网接口专项测试，不代表这些测试已运行。

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m pytest -q
```

## 后端入口

```python
from simulator import ActionType, GameConfig, GameState

game = GameState.new_game(config=GameConfig(allow_multiple_hu=False))
tile = game.draw_tile(game.current_player().player_id, now=0)
# 根据当前合法选项提交响应、杠牌、自摸或出牌。
# 自摸: game.submit_turn_action(player_id, ActionType.HU, now=...)
# 抢杠响应: game.submit_rob_kong_response(player_id, ActionType.HU, now=...)
# 结算: game.round_result / game.to_public_dict()["round_result"]
# 下一局: game.start_next_round(source_round_id=game.round_id)
```

GameState 不自行等待时间；外部运行层调用 `expire_timers(now)` 推进超时。下一局仅在 `ROUND_ENDED` 后接受手动请求。

当前房间与人机专项测试：`python -m pytest multiplayer/test -q`。可选浏览器检查为 `python -m multiplayer.check_live` 和 `python -m multiplayer.check_bot_play`，需要额外安装 Playwright；截图写入忽略的 `multiplayer/.runtime/`。网页运行不需要 Playwright。

## 设计文档

- [总设计与实现进度](design_docs/general_design.md)
- [Action 与计时](design_docs/action_timeout_design.md)
- [手牌分组](design_docs/hand_grouping_design.md)
- [旧单人 UI 与开发者牌堆（归档）](design_docs/ui_prototype_design.md)
- [四人联机设计与实施计划](design_docs/multiplayer_design.md)
- [个人电脑开服、重置与断线托管](design_docs/hosting_design.md)
- [引擎保存恢复与旧服务记录](design_docs/state_recovery_design.md)
- [Bot 接口与分阶段计划](design_docs/bot_design.md)
- [Bot 开发与验收日志](test_docs/bot_development_log.md)
- [评测报告索引](test_docs/README.md)
- [本地人机试玩、CPU 预算与训练隔离](design_docs/bot_play_design.md)
- [血战规则草案](design_docs/blood_battle_design.md)
