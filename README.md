# Arithmetic-Mahjong-Simulator

算术麻将单机版，包含 Python 牌局后端与本地人机牌桌。支持普通与血战模式、点胡、自摸、抢杠、流局结算和手动下一局。已提供 Bot、Arena 与实验训练，以及独立的本地 RL 人机试玩入口。

## 双击游玩（无需 Python）

Windows 用户可直接使用 `dist/算术麻将.exe`，也可将 `dist/算术麻将-Windows独立窗口版.zip` 发给其他人。解压后双击 exe，牌桌直接显示在独立游戏窗口中；不需要安装 Python、打开浏览器或输入命令，内置 NN、RL 和 CFR 模型，可离线运行。关闭游戏窗口即可退出，退出后本次对局清空。

使用说明见 [Windows 发布说明](windows/README.md)。下面的 Python 命令用于开发和训练环境。

当前源码启动后先显示带 Logo 的模式首页，可选择新手教程、普通模式、教学模式、计算模式和听牌练习。普通模式进入现有单机对局；听牌练习已支持随机出题、多选判题、限时或不限时、拖牌整理，以及全部待牌和分组解答。新手教程、教学模式和计算模式目前提供界面预览。页面提供返回首页入口，当前对局会保留并继续计时。源码修改默认只验证，不自动重新生成 exe；发布文件以最近一次明确打包为准。

听牌练习每次从完整 108 张牌中无放回随机抽取 13 张，只检查牌型，不考虑起胡番。按不同待牌牌种数做拒绝采样：不听接受率 1%，单待牌接受率 10%，两种及以上全部接受。空听也计入并须选出，答案会标注副本已全部在手中。默认每题限时 30 秒，可调整为 1～600 秒或不限时；提交、查看答案或超时后展示全部正确待牌，可展开查看分组，金色边框表示补入的牌。

## 本地人机试玩

安装 Python 3.13 后，在仓库根目录打开 PowerShell。首次运行先创建运行环境并安装依赖，无需 PyTorch、CUDA 或独立显卡：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r singleplayer/requirements.txt
.\.venv\Scripts\python.exe -m singleplayer --port 8772 --model models/rl-3h/snapshots/ppo-0068.npz --acfr-model models/cfr-abstract-newrules-1h/policy.json.gz
```

上述命令显式选择 RL 第 68 批及累计训练 26,794 局的CFR，不依赖作者机器上的 RL 缓存。根目录 `models/` 存放可随源码分发的冻结权重，`.data/` 仅保存本地训练数据并整目录忽略。桌面发布使用下面三份推理导出；其他训练数据、检查点和评测文件均不是游玩依赖：

```text
models/p4-v1/policy.npz
models/rl-3h/snapshots/ppo-0068.npz
models/cfr-abstract-newrules-1h/policy.json.gz
```

CFR 的目录名沿用训练时的 `-1h`，实际已经续训到累计约五小时。`models/cfr-abstract-v1/policy.json.gz` 和 `models/rl-v1/policy.npz` 保留既有默认入口的选择；省略 `--acfr-model` 时仍加载旧 CFR。权重位置和默认用途见 [游玩模型说明](models/README.md)。

没有模型时也可运行 `.\.venv\Scripts\python.exe -m singleplayer --port 8772 --no-models`，使用基础、数学、搜索和启发式风格策略。

访问 [单机首页](http://127.0.0.1:8772)，选择“普通模式”后打开单机设置，默认由你和三家 Bot 对局。设置提供以下 7 类、共 21 个配置，名称与 UI 一致：

| UI 分类 | 数量 | 如何决策 |
| --- | ---: | --- |
| 基础 | 3 | 摸切、随机、先胡随机，作为简单对手。 |
| 数学 | 1 | 根据听牌、未见张数和手牌结构进行计算与评分，无需训练模型。 |
| 搜索 | 1 | 以数学策略为基础，在预算内额外分析少量出牌候选，试算距离、进张并模拟后续对局；当前只对出牌做有限搜索。 |
| 启发式风格 | 4 | 用手写的推进、牌值、风险和牵制评分形成不同打法，无需训练模型。 |
| 模仿学习 NN | 4 | 神经网络学习启发式教师给出的动作偏好，使用模仿训练得到的权重。 |
| 强化学习 RL | 4 | 从模仿模型出发，通过对局奖励和风格目标继续训练神经网络，使用独立的 RL 权重。 |
| CFR | 4 | 把相似的可见局面合并为有限类别，学习在“推进、牌值、安全、牵制、均衡”五种选牌规则之间取舍，再由规则选出具体合法动作。 |

表中数量为 4 的类别均提供**速胡、做大、稳守、牵制**四种风格。类别表示实现方法，不是强弱等级。

**NN 和 RL 都使用神经网络。** 这里 UI 中的“NN”特指模仿学习版，主要学习教师怎么选；“RL”则在此基础上根据对局奖励继续调整。两者的游戏内推理结构相同，训练方式和加载的权重不同，RL 不自动代表更强。NN、RL 和CFR 在试玩中都读取冻结模型，不会一边打牌一边继续训练。

**“搜索”会额外做有限的后续对局模拟，“启发式风格”主要通过预设评分体现打法差异。** 搜索只在预算允许时分析少量出牌候选，本身也使用启发式评分；预算不足时会保留数学策略的可用选择。

**CFR 沿用原来的抽象 CFR 算法和训练权重，本次仅改显示名称。** 它合并相似局面、缩短历史，学习五种选牌规则的概率；未命中时使用对应风格规则。它只使用玩家有权知道的信息，不读取真实对手暗牌或未来牌山。当前仍为近似采样训练实验，没有最优策略或均衡保证。内部 `acfr-*` 配置名、训练模块和模型路径保持兼容。原精确版入口及专用产物已移除。

缺少模型的项会在 UI 中禁用，不影响无需模型的策略开局。支持拖动排序、按实体牌出牌及吃碰杠胡过。每局发牌后默认先理牌 30 秒，可在开局前设为 0～600 秒，0 表示跳过；理牌期间每家 13 张，倒计时结束后庄家摸牌。默认操作计时为 t1/t2/t4 各 5 秒、t3 30 秒，可在每局开局前调整或关闭；理牌时间单独设置。

可在每局开局前修改普通/血战模式、一炮多响和计时；本局进行时锁定，结算后点“下一局”即可重新设置，确认开局后生效并保留累计分数。普通模式同样支持一炮多响开关。血战中胡牌即时结算并退出，第三家胡牌或牌山摸空结束；自摸只向未胡的对手收款，一炮多响分别由点炮者付款。顶部“胡牌记录”可在中途查看结算，整局结束后可在结算页点击“查看牌山”，安排下一局期间仍可回看，确认新开局后关闭。现有 Bot 尚未针对血战单独训练或优化。

可与 RL 长训同时运行：默认重启沿用私有缓存中的冻结 RL，当前已更新为训练器开发验证选中的第 68 轮（最后完成的是第 71 轮）；不读取训练正在更新的别名或检查点，不自动更新。NN 和CFR 各有独立冻结模型。明确传 `--model <编号快照>` 才更换 RL；传训练目录会选最新完成批，不等于验证选中批。`--no-models` 可完全不读模型。只用一个低优先级 CPU 推理进程、不导入 Torch/CUDA，不修改训练源码或输出，但同机仍会有少量资源竞争。当前环境已安装所需依赖，新环境可装 `singleplayer/requirements.txt`，不要改训练虚拟环境。

只监听本机，所有本机标签页共用同一张单机牌桌；关闭程序后重置，刷新页面继续当前对局。启动脚本为 `.\singleplayer\start.ps1`，可加 `-NoModels` 使用无需模型的策略。详细预算与模型规则见 [单机人机设计](design_docs/bot_play_design.md)。

## 实现状态

- ✅ 四人发牌、摸牌、按实体牌出牌、手牌拖动排序。
- ✅ t1 响应裁决、t2 吃法选择、t3 暗杠/加杠/自摸及超时处理。
- ✅ 仅加杠可抢的 t4，全部过恢复原 t3 剩余时间；只有合格玩家能操作，倒计时只对本人可见。
- ✅ 可替换的 RuleAdapter、NormalScoringAdapter 与 BloodBattleScoringAdapter。
- ✅ 最高番胡法、番种明细、赔付、分数变化和 RoundResult。
- ✅ 本人本局首次出牌前胡牌自动加天胡 3 番；点胡由点炮者付款，自摸由其他仍参与的玩家付款。
- ✅ 七小对要求七种不同牌各两张，大三元要求至少 9 张符号；数学、搜索、规则风格及CFR 辅助逻辑已同步。
- ✅ 一炮多响开关、累计分数、手动开始下一局和庄家固定轮换。
- ✅ 血战胡牌退出、逐次付款、三家胡牌/流局汇总、即时记录与旧普通快照迁移。
- ✅ t1～t4 均可拖动排序，包括自己的吃牌选择；实体 ID、本局及本人手牌版本防止旧拖动误移牌，非法位置不产生部分修改。自动吃碰杠从当前手牌左侧选取，手动吃牌保留指定实体。
- ✅ 完整 GameState 私有快照及 SQLite 原子存储能力；当前单机入口使用内存状态，关闭程序后重置。
- ✅ 独立 Bot P0/P1：玩家观察、完整候选、控制权校验、三个基线及支持计算预算/超时保底的四席 Arena。
- ✅ Bot P2 首版：预算内听牌分析、未见牌统计、组形启发式、吃碰杠比较及逐动作评分说明。
- ✅ Bot P3 实验版本：库存约束的距离/进张、固定待牌概率、可见库存采样及 t3 出牌截断搜索。
- ✅ Bot P3.5 初级风格：速胡、做大、稳守及可调牵制，共用分析、计算预算和候选说明。
- ✅ Bot P4 模仿首版：独立 CUDA 训练、带风格候选网络、CPU 推理、冻结快照与同预算降级。
- ✅ Bot PPO 实验：风格效用、可见信息 critic、旧策略池、精确行为概率、恢复与独立 RL 模型。
- ✅ 独立本地全策略人机试玩：21 个配置、NN/RL/CFR 独立冻结模型、默认不更新 RL、单 CPU 进程与预算保底，保留真人命令路径。

## Bot 离线 Arena

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m arena --games 4 --bots math tsumogiri random win-random --output .data/arena/report.json
```

默认快速跑局，四席轮换，每次 bot 计算上限 500 ms；`--mode timed` 使用实际 t1～t4，`--compute-ms` 设置计算预算。包含摸切、随机、先胡再随机三个基线，输出筹码收益、耗时与超时/保底统计。独立进程超时后回收，该席本局使用保底，下一局重新准备策略。当前入口不接管真人桌。

`math` 是 P2 数学策略：复用规则黑箱求听牌与付款，结合未见张数和多胡型组形评分，在预算不足时返回当前最佳候选。它也比较吃碰杠，但尚无完整期望收益或校准的防守模型。加 `--trace .data/arena/trace.jsonl` 可查看候选评分、待牌和分析完成状态；详细初测见 [Bot 设计与评测记录](design_docs/bot_design.md)。

将 `--bots` 改为 `search math win-random random` 可试用 P3。`search` 在相同计算上限内，对 P2 的部分出牌候选进行距离/进张分析和成批假想牌局模拟；预算不足时保留当前可用选择。当前仅搜索 t3 出牌，属于有界启发式实验，尚无完整收益求解或行为后验。P2 保留为独立策略供对照。

将 `--bots` 改为 `fast big defensive math` 可试用 P3.5 的速胡、做大和稳守风格；`blocking` 是稳守加高牵制的预设。这些初级策略不需要训练，所有风格接受合法胡牌，目标是有可辨认的打法。风险/喂牌指标尚未校准，真人 UI 接入和实际陪玩体验仍待验证；使用方法见 [Arena 使用说明](arena/README.md)。

自定义 `Bot.act(observation, legal_actions)`、有限时示例和私有轨迹记录见 [Arena 使用说明](arena/README.md)。

P4 已在本机 RTX 3060 上训练，可用 `.\.venv-bot\Scripts\python.exe -m arena --games 4 --bots nn-fast nn-big nn-defensive nn-blocking` 试跑。模型位于 `models/p4-v1/policy.npz`；缺少模型时会明确降级为对应初级风格。训练使用 GPU，对局使用小型 NumPy CPU 模型并遵守原有计算上限。模仿版安装、重训与恢复见 [训练说明](training/README.md)。


## 测试

使用 Python 3.13，并在仓库根目录运行：

测试环境需安装 pytest、httpx 和单机运行依赖。运行引擎与单机专项检查：

```powershell
.\.venv\Scripts\python.exe -m pytest test singleplayer/test -q
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

单机专项覆盖固定三家 Bot、配置锁定、旧请求拒绝、普通/血战整局、结算牌山、下一局和本机 HTTP 接口。测试目录和运行缓存继续本地忽略。

## 设计文档

- [总设计与实现进度](design_docs/general_design.md)
- [Action 与计时](design_docs/action_timeout_design.md)
- [手牌身份、排序与动作选牌](design_docs/hand_ordering_design.md)
- [旧单人 UI 与开发者牌堆（归档）](design_docs/ui_prototype_design.md)
- [引擎保存恢复与牌山记录](design_docs/state_recovery_design.md)
- [Bot 接口与分阶段计划](design_docs/bot_design.md)
- [Bot 开发与验收日志](test_docs/bot_development_log.md)
- [评测报告索引](test_docs/README.md)
- [单机人机对局、CPU 预算与模型隔离](design_docs/bot_play_design.md)
- [血战规则与实现](design_docs/blood_battle_design.md)
