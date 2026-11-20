# 算术麻将单机版

用麻将牌凑加法、乘法算式或刻子，和三家 Bot 对局。支持普通模式、血战、六步新手教程、教学辅助、手牌计算、听牌练习及复盘回放，可离线游玩。

## 游玩方式

已打包的 Windows 版本包含 `算术麻将.exe` 和使用说明，双击即可运行，不需要 Python、浏览器或显卡计算环境。exe 和 ZIP 属于发布产物，不包含在源码仓库中，可作为 GitHub Release 附件单独分发。

下载本仓库源码后，可以按下文运行，也可以自行选择打包或安装工具。Windows 构建步骤见 [Windows 发布说明](windows/README.md)。

## 从源码运行

使用 Python 3.13，在仓库根目录执行。游玩只需要 CPU，不需要 PyTorch 或 CUDA，也不需要训练模型。

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r singleplayer/requirements.txt
.\.venv\Scripts\python.exe -m singleplayer --model models/rl-3h/snapshots/ppo-0068.npz --acfr-model models/cfr-abstract-newrules-1h/policy.json.gz
```

### Linux / macOS

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r singleplayer/requirements.txt
.venv/bin/python -m singleplayer --model models/rl-3h/snapshots/ppo-0068.npz --acfr-model models/cfr-abstract-newrules-1h/policy.json.gz
```

运行后，在浏览器打开终端显示的本机地址，默认是 `http://127.0.0.1:8772`。程序只监听本机，所有本机标签页共用同一张牌桌；刷新可继续对局，关闭程序后本次进度和累计分数清空。

上述命令显式选择仓库内的 RL 和 CFR 权重，NN 默认从 `models/p4-v1/policy.npz` 加载。可加 `--port 8773` 修改端口，或使用 `python -m singleplayer --no-models` 只启用无需模型的策略；`python` 应替换成当前虚拟环境的解释器路径。

### 独立窗口与打包

Windows 上可直接使用已有构建流程：

```powershell
.\.venv\Scripts\python.exe -m pip install -r windows/requirements-build.txt
.\.venv\Scripts\python.exe -m windows.launcher
.\.venv\Scripts\python.exe tools/build_windows.py
```

第二条命令从源码打开独立窗口；第三条生成 `dist/算术麻将.exe` 和 `dist/算术麻将-Windows独立窗口版.zip`，并执行成品自检。

自动构建脚本面向 Windows 10/11 x64。Linux/macOS 可运行上面的本地浏览器入口，或在对应系统安装 PySide6 后使用同一个 `windows.launcher` 独立窗口入口，自行选择打包工具；仓库暂未提供这两种系统的成品。

## 模式与功能

| 模式 | 内容 |
| --- | --- |
| 新手教程 | 六课固定示范：基础知识、拖牌胡牌、吃碰杠、传统麻将、七小对和清龙、血战。 |
| 普通模式 | 你和三家 Bot 对局，可选普通或血战规则，调整操作计时及开局理牌时间。 |
| 教学模式 | 查看四家手牌、暂停思考，分析任意一家的向听和出牌路线。 |
| 计算模式 | 13 张看待牌与有效进张；14 张比较出牌，可录入已见牌并模拟后续摸打。 |
| 听牌练习 | 判断 13 张牌的待牌或不能听，支持拖牌、限时/不限时及全部分组解答。 |
| 复盘回放 | 逐步查看已结束的对局，支持视角、倍速、历史分析和 JSON 导入/导出。 |

普通和教学模式共用当前对局，返回首页不会自动暂停计时。每局默认先理牌 30 秒，可设为 0～600 秒。所有模式均有离线规则速查，可查询规则、牌池数量和番数。

教学分析默认持续续算，展示最低向听候选的前 5 项，可选前 3、前 10 或全部。评价采用连续降低向听的进张 DP，按未见牌数量无放回加权，支持起胡番和剩余自摸次数；不计防守风险、无效摸牌等待或对手行动，因此该值不是整局胡牌率或实际收益。

听牌练习默认每题 30 秒，可取消限时；按不同待牌牌种数做拒绝采样：不听接受率 1%，单待牌接受率 10%，两种及以上全部接受。默认不考虑起胡番，空听也列出并标注。

血战中胡牌即时结算并退出，第三家胡牌或流局时整局结束；自摸只由当时仍在场的其他玩家赔付。回放在本次运行中保留最近 20 局，退出前请导出需要保留的牌谱。

## Bot 与模型

提供基础、数学、搜索、启发式风格、NN、RL、CFR 共 7 类、21 个配置。NN/RL/CFR 使用冻结权重，仅做 CPU 推理；缺少模型时对应选项会禁用，其余策略仍可使用。Bot 按自身可见信息决策，不读取对手暗牌或未来牌山顺序。

`models/` 随源码保留游玩需要的导出和校验清单，无需重新训练。桌面版默认使用：

```text
models/p4-v1/policy.npz
models/rl-3h/snapshots/ppo-0068.npz
models/cfr-abstract-newrules-1h/policy.json.gz
models/manifest.json
```

另保留通用 Bot 工厂和命令行原有的兼容权重，具体用途见 [游玩模型说明](models/README.md)。

## 仓库内容与自定义分发

| 目录 | 用途 |
| --- | --- |
| `singleplayer/` | 本地服务、对局、教程、练习、分析与回放；六课文案统一在 `singleplayer/tutorial.py`。 |
| `simulator/`、`calculator_base/`、`fan_calculator/` | 牌局状态、胡牌与计番规则。 |
| `bots/` | 游玩用 Bot 与 CPU 推理逻辑。 |
| `UI/static/`、`pictures/` | 页面、脚本、样式、图标及牌面资源。 |
| `models/` | 冻结推理权重及清单。 |
| `windows/`、`tools/` | 独立窗口入口、构建依赖、打包配置与脚本。 |
| `design_docs/` | 设计说明与更新记录，随源码保留。 |

自定义打包时，除 Python 代码和依赖外，还需包含 `UI/static/`、`pictures/`、`models/`，并保留清单中的相对路径。程序使用子进程，冻结程序入口需调用 `multiprocessing.freeze_support()`；现有 `windows/launcher.py` 可作为入口参考。

训练、Arena、测试源码和本地实验数据不属于游玩分发范围。`.gitignore` 排除 `.data/`、运行缓存、虚拟环境及 `build/`、`dist/` 等产物，`git add .` 会保留运行源码、模型、资源和设计文档。

创建 `build/` 后，可使用虚拟环境解释器运行源码后端自检：

```text
python -m windows.launcher --self-test build/source-self-test.json
```

自检覆盖六课教程、练习与计算、三类模型、21 个策略、开局及退出；Windows 构建流程还会检查实际独立窗口。

## 规则与设计说明

- [算术麻将规则简单版](算术麻将规则简单版.pdf)
- [总设计与实现进度](design_docs/general_design.md)
- [单机对局、CPU 预算与模型](design_docs/bot_play_design.md)
- [教程与规则速查](design_docs/tutorial_reference_design.md)
- [手牌计算与评价](design_docs/calculator_design.md)
- [教学模式与复盘](design_docs/teaching_replay_design.md)
- [血战规则与实现](design_docs/blood_battle_design.md)
- [更新日志](design_docs/update_log.md)
