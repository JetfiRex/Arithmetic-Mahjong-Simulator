# Arithmetic-Mahjong-Simulator

算术麻将的 Python 牌局后端、本地真人房间及人机牌桌。支持普通与血战模式、点胡、自摸、抢杠、流局结算和手动下一局。已提供 Bot、Arena 与实验训练，以及独立的本地 RL 人机试玩入口。

## 本地人机试玩

安装 Python 3.13 后，在仓库根目录打开 PowerShell。首次运行先创建运行环境并安装依赖，无需 PyTorch、CUDA 或独立显卡：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r multiplayer/requirements-bot.txt
.\.venv\Scripts\python.exe -m multiplayer.bot_play --port 8772 --model .data/models/rl-3h/snapshots/ppo-0068.npz --acfr-model .data/models/cfr-abstract-newrules-1h/policy.json.gz
```

上述命令显式选择 RL 第 68 批及累计训练 26,794 局的新版抽象 CFR，不依赖作者机器上的 RL 缓存。发布时需一同提供下面四份推理导出；其他训练数据、检查点和评测文件均不是游玩依赖：

```text
.data/models/p4-v1/policy.npz
.data/models/rl-3h/snapshots/ppo-0068.npz
.data/models/cfr-mahjong/policy.json.gz
.data/models/cfr-abstract-newrules-1h/policy.json.gz
```

新抽象 CFR 的目录名沿用训练时的 `-1h`，实际已经续训到累计约五小时。原 `.data/models/cfr-abstract-v1/policy.json.gz` 可保留作旧版对照；省略 `--acfr-model` 时仍加载这个旧默认文件。

没有模型时也可运行 `.\.venv\Scripts\python.exe -m multiplayer.bot_play --port 8772 --no-models`，使用基础、数学、搜索和启发式风格策略。

访问 [8772 人机试玩](http://127.0.0.1:8772)，创建房间后默认安排你和三家 Bot。房主设置提供以下 8 类、共 25 个配置，名称与 UI 一致：

| UI 分类 | 数量 | 如何决策 |
| --- | ---: | --- |
| 基础 | 3 | 摸切、随机、先胡随机，作为简单对手。 |
| 数学 | 1 | 根据听牌、未见张数和手牌结构进行计算与评分，无需训练模型。 |
| 搜索 | 1 | 以数学策略为基础，在预算内额外分析少量出牌候选，试算距离、进张并模拟后续对局；当前只对出牌做有限搜索。 |
| 启发式风格 | 4 | 用手写的推进、牌值、风险和牵制评分形成不同打法，无需训练模型。 |
| 模仿学习 NN | 4 | 神经网络学习启发式教师给出的动作偏好，使用模仿训练得到的权重。 |
| 强化学习 RL | 4 | 从模仿模型出发，通过对局奖励和风格目标继续训练神经网络，使用独立的 RL 权重。 |
| 精确 CFR | 4 | 按较完整的玩家可见观察、个人决策历史和具体合法动作查平均策略表；局面区分细，未见局面回退到对应风格规则。 |
| 抽象 CFR | 4 | 把相似的可见局面合并为有限类别，学习在“推进、牌值、安全、牵制、均衡”五种选牌规则之间取舍，再由规则选出具体合法动作。 |

表中数量为 4 的类别均提供**速胡、做大、稳守、牵制**四种风格。类别表示实现方法，不是强弱等级。

**NN 和 RL 都使用神经网络。** 这里 UI 中的“NN”特指模仿学习版，主要学习教师怎么选；“RL”则在此基础上根据对局奖励继续调整。两者的游戏内推理结构相同，训练方式和加载的权重不同，RL 不自动代表更强。NN、RL 和两类 CFR 在试玩中都读取冻结模型，不会一边打牌一边继续训练。

**“搜索”会额外做有限的后续对局模拟，“启发式风格”主要通过预设评分体现打法差异。** 搜索只在预算允许时分析少量出牌候选，本身也使用启发式评分；预算不足时会保留数学策略的可用选择。

**精确 CFR 的“精确”指信息集划分更细，不是已经求出了麻将的精确最优解。** 它保留更多可见观察和个人历史，直接学习具体动作的概率；只有再次遇到相同的可见观察、个人决策历史和动作菜单，才能命中原表项。抽象 CFR 主动合并局面、缩短历史，并学习五种选牌规则的概率，查表更容易复用，但会丢失部分细节。两者都只能使用玩家有权知道的信息，不读取真实对手暗牌或未来牌山；当前均为采样训练实验，不代表已获得最优策略。

缺少模型的项会在 UI 中禁用，不影响无需模型的策略开局。拖牌、分组、吃碰杠胡过仍用原操作。默认计时为 t1/t2/t4 各 5 秒、t3 30 秒，可在每局开局前调整或关闭。

房主可在每局开局前修改普通/血战模式、一炮多响和计时；本局进行时锁定，结算后点“下一局”即可重新设置，确认开局后生效并保留累计分数。普通模式同样支持一炮多响开关。血战中胡牌即时结算并退出，第三家胡牌或牌山摸空结束；自摸只向未胡的对手收款，一炮多响分别由点炮者付款。顶部“胡牌记录”可在中途查看结算，整局结束后可在结算页点击“查看牌山”，安排下一局期间仍可回看，确认新开局后关闭。现有 Bot 尚未针对血战单独训练或优化。

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
- ✅ 可替换的 RuleAdapter、NormalScoringAdapter 与 BloodBattleScoringAdapter。
- ✅ 最高番胡法、番种明细、赔付、分数变化和 RoundResult。
- ✅ 本人本局首次出牌前胡牌自动加天胡 3 番；点胡由点炮者付款，自摸由其他仍参与的玩家付款。
- ✅ 七小对要求七种不同牌各两张，大三元要求至少 9 张符号；数学、搜索、规则风格及抽象 CFR 辅助逻辑已同步。
- ✅ 一炮多响开关、累计分数、手动开始下一局和庄家固定轮换。
- ✅ 血战胡牌退出、逐次付款、三家胡牌/流局汇总、即时记录与旧普通快照迁移。
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
- ❌ 完整搜索/CFR Bot、血战专用策略训练、Access 身份绑定/跨设备找回、Tunnel/公网部署及逐步回放播放器。

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
from simulator import ActionType, GameConfig, GameMode, GameState

game = GameState.new_game(config=GameConfig(allow_multiple_hu=False))
# 血战可指定 GameConfig(mode=GameMode.BLOOD_BATTLE)。
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
- [血战规则与实现](design_docs/blood_battle_design.md)
