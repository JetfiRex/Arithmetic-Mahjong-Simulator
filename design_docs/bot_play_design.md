# 单机人机对局设计

本目录为单机版。唯一运行入口为 `python -m singleplayer --port 8772`，浏览器访问 `http://127.0.0.1:8772`。依赖见 `singleplayer/requirements.txt`，也可运行 `singleplayer/start.ps1`。

## 对局流程

- 固定一名玩家（p0）和三家 Bot（p1～p3），打开页面直接进入单机设置。
- 保留基础、数学、搜索、启发式风格、NN、RL 和 CFR 共 21 个配置。没有模型的配置禁用；`--no-models` 可直接游玩无需模型的策略。
- 开局前选择三家对手策略、普通/血战模式、一炮多响和 t1～t4。默认 t1/t2/t4 为 5 秒，t3 为 30 秒，也可关闭计时。
- 每局发完 13 张手牌后先进入理牌阶段，默认 30 秒，开局前可设为 0～600 秒，0 表示跳过；下一局沿用上次设置，也可重新调整。期间可以拖动排序，Bot 不行动，倒计时结束后庄家才摸牌并开始 t3。理牌计时不受操作计时开关控制。
- 本局中锁定配置；结算后点击“下一局”进入安排阶段，确认开局时应用新设置，保留累计分数并轮庄。
- 保留按实体牌出牌、跨阶段拖动排序、吃碰杠胡过、抢杠与 t4 结束后的剩余 t3。
- 血战保留胡牌退出、即时付款、胡牌记录、第三家胡牌或牌山摸空结束。当前策略未针对血战单独训练。
- 结算及安排下一局期间可查看完整原始牌序，确认新开局后旧局查询失效；进行中不下发牌山或其他暗手。
- 所有本机标签页共用同一张牌桌。刷新不重置；关闭 Python 程序后重置，不自动载入旧 SQLite 存档。

## 模型与资源

三类学习策略分别冻结模型，不读取训练检查点，不热更新，不修改训练产物。

```powershell
.\.venv\Scripts\python.exe -m singleplayer --port 8772 --model models/rl-3h/snapshots/ppo-0068.npz --acfr-model models/cfr-abstract-newrules-1h/policy.json.gz
.\.venv\Scripts\python.exe -m singleplayer --port 8772 --no-models
```

- NN 默认 `models/p4-v1/policy.npz`；CFR 默认 `models/cfr-abstract-v1/policy.json.gz`，可用 `--acfr-model` 指定续训版本。
- `--model` 显式选择 RL 的已完成编号快照；传训练目录会选最新完成的 `snapshots/ppo-N.npz`，不等于开发验证选中的版本。
- 省略 `--model` 只读 `singleplayer/.runtime/rl-selection.json` 中记住的冻结副本并校验 SHA；无记录或记录损坏则禁用 RL，不跟随训练自动更新。
- 私有副本位于 `singleplayer/.runtime/play-models/`。本次目录迁移保留原冻结副本、摘要与选择记录，不更换模型。
- 一个低优先级 CPU 推理进程供三家串行复用，不导入 Torch/CUDA。默认计算预算 500 ms，`--compute-ms` 接受 10～1000 ms，并受实际牌局剩余时间限制。
- 每个窗口约 0.4 秒可读停顿，不延长游戏计时。结果按实际计算完成时间判断是否超算，提交时再次校验当前轮次与窗口。
- 超算时终止本程序自己的推理子进程，本局后续使用合法保底；下一局确认开局时重新准备。
- Bot 只取得本席可见观察与合法候选，不持有 GameState，不读取对手暗牌或未来牌山。真人操作固定作用于 p0。
- 引擎计时、真人命令和 Bot 提交在同一把锁内串行处理；耗时策略在独立进程计算。

## 文件与验证

| 文件 | 用途 |
| --- | --- |
| `singleplayer/__main__.py` | 本机启动与模型准备 |
| `singleplayer/runtime.py` | 单桌状态、配置、计时、Bot 调度、玩家命令与视图 |
| `singleplayer/web.py` | 本机页面、状态、动作及赛后牌山接口 |
| `singleplayer/bot_catalog.py` | 21 个策略配置与模型可用性 |
| `singleplayer/bot_worker.py` | CPU 子进程与硬截止 |
| `singleplayer/play_model.py` | 模型冻结、RL 选择记忆与校验 |
| `singleplayer/round_review.py` | 完整原始牌序与实际取牌记录 |
| `singleplayer/settings.py` | 理牌时间、t1～t4 默认值与验证 |
| `UI/static/` | 单机牌桌、设置与交互 |

本机页面通过 HTTP 调用本机 Python 引擎，程序只绑定 `127.0.0.1`。不使用 WebSocket。运行层不包含大厅、成员身份、密码、多人座位分配、观战、断线宽限或托管。

检查命令：`python -m pytest test singleplayer/test -q`。专项检查涵盖固定控制权、策略工厂、缺模型、配置锁定、去重与旧请求、手牌版本、正常/血战整局、下一局、牌山边界及 HTTP 接口。各模型强度、长局稳定性和风格辨识度仍需独立评测。
