# Arithmetic-Mahjong-Simulator

算术麻将的 Python 牌局后端、本地四人开发者局及单人试牌 UI。普通模式支持点胡、自摸、抢杠、流局结算和手动下一局。策略 AI 与血战暂未实现。

## 开发者 UI

在仓库根目录启动：

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m UI.server --port 8766
```

当前已启动的新版四人预览在 [8766 端口](http://127.0.0.1:8766)。端口不是模式开关，请以所启动服务打印的 Local 地址为准，在新版的开局设置中选择玩法。

支持拖动、右键分组、吃碰杠胡过、摸牌、出牌和结算。右侧完整牌堆按“下一张在最前”显示，可拖动重排、移到最前、删除或编辑 JSON 数组；自己的手牌也可编辑。

默认进入开局设置：t1/t2/t4 为 5 秒，t3 为 30 秒，可单独改时长、关闭某项或全局不计时。**开局后锁定配置**；需显式结束本局并重新配置才能修改。

四人开发者局中你操作一家，其余三家真实摸牌、固定约 1 秒后打最右张，对吃碰杠胡和抢杠一律过。状态机会自动推进轮转；倒计时仍只显示你自己的。也可在开局前切回原来的单人试牌台并选择预置场景。完整设计与限制见 [UI 小样设计](design_docs/ui_prototype_design.md)。服务退出后牌局不保留。

四家弃牌分别显示在各自区域。启用 t1 时，每一家出牌后都必须等待完整响应时长，即使所有人已提前过也不会跳过；关闭 t1 后才在所有响应提交后立即裁决。

对手不会加杠，所以玩家不会自然遇到抢杠；玩家加杠时若对手能抢，仍创建真实 t4 并由对手过掉。

## 实现状态

- ✅ 四人发牌、摸牌、按实体牌出牌、手牌排序和自定义分组。
- ✅ t1 响应裁决、t2 吃法选择、t3 暗杠/加杠/自摸及超时处理。
- ✅ 仅加杠可抢的 t4，全部过恢复原 t3 剩余时间；只有合格玩家能操作，倒计时只对本人可见。
- ✅ 可替换的 RuleAdapter 和普通模式 NormalScoringAdapter。
- ✅ 最高番胡法、番种明细、赔付、分数变化和 RoundResult。
- ✅ 一炮多响开关、累计分数、手动开始下一局和庄家固定轮换。
- ✅ 整理与出牌请求支持旧 t3 窗口校验，非法跨组移动不会产生部分修改。
- ✅ 本地 solitaire UI、可编辑牌堆、预置试牌场景与普通结算展示。
- ✅ 四人开发者局、自动过牌对手、开局计时设置及锁定、后台超时推进与阶段记录。
- ❌ 策略 AI、血战、正式联机与持久化。

## 测试

使用 Python 3.13，并在仓库根目录运行：

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

可选浏览器检查：安装 Playwright 后运行 `python -m UI.check_ui`，使用本机 Edge 检查桌面/窄屏与关键交互。截图写入忽略的 `UI/.runtime/`；页面运行本身不需要 Playwright。

四人局专项浏览器检查：`python -m UI.check_four_player_ui`。

## 设计文档

- [总设计与实现进度](design_docs/general_design.md)
- [Action 与计时](design_docs/action_timeout_design.md)
- [手牌分组](design_docs/hand_grouping_design.md)
- [单人 UI 与开发者牌堆](design_docs/ui_prototype_design.md)
- [血战规则草案](design_docs/blood_battle_design.md)
