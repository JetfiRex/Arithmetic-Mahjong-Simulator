# 算术麻将在线单人平台设计文档

本文档记录第三阶段的设计目标：在已有判胡、听牌、计番能力之上，构建一个可以完整运行单局游戏的牌局引擎。当前阶段暂不追求完整网页界面、账号系统或高级 AI，而是先把“牌局怎么流动”定义清楚。

## 阶段定位

第一、二阶段已有能力可以视为规则黑箱：

- 判断手牌是否胡牌。
- 计算番数、封顶番、赔付筹码。
- 判断听牌。
- 解析手牌和牌组。

第三阶段不继续深入番种内部实现，而是建设游戏状态机。游戏状态机只关心一局游戏中发生了什么：发牌、摸牌、打牌、吃碰杠胡、响应优先级、流局和结算。

核心原则：

> 牌局引擎负责“流程是否合法”；规则黑箱负责“牌型是否成立、值多少番”。

这样以后调整番种、起胡规则或局部规则时，只需要替换规则适配层，不需要重写整套牌局流程。

普通模式与血战模式共用同一套基础状态机。血战模式的退出玩家、一炮多响、即时结算和继续摸牌规则详见 `blood_battle_design.md`。

## 第三阶段目标

第三阶段完成后，应当能够在没有正式前端的情况下，通过测试或命令行模拟跑完一局。

最小可用目标：

1. ✅ 创建一局四人游戏，并可通过脚本或测试动作跑到普通胡牌结算。（现用入口为纯真人房间与支持多种 Bot 的人机试玩）
2. ✅ 根据牌池生成完整牌山并洗牌。
3. ✅ 发牌并确定庄家、当前行动玩家。
4. ✅ 支持摸牌、打牌。
5. ✅ 支持自摸、点胡、抢杠及结算。（仅加杠可抢）
6. ✅ 支持吃、碰、明杠、暗杠、加杠及加杠抢杠响应。
7. ✅ 支持别人打牌后的响应窗口。
8. ✅ 正确处理冲突优先级：胡 > 杠 > 碰 > 吃。
9. ✅ 支持多人同时胡牌和分别结算，也可关闭一炮多响、只保留最近的赢家。
10. ✅ 支持牌山摸空后的流局。
11. ✅ 胡牌时调用计番黑箱并生成结算结果。
12. ✅ 暴露足够清晰的状态快照，供未来前端渲染。（基础快照已实现）
13. ✅ 支持普通模式与血战模式配置，并按模式决定胡牌后结束还是继续。
14. ✅ 提供手动开始下一局的后端入口，并固定将庄家轮换到下家。（当前房间牌桌已接入结算 UI）

## 前置准备

虽然判胡和计番已有基础，但进入第三阶段前建议先准备以下边界。

### ✅ 统一牌表示

牌局引擎内部建议统一使用简单牌值：

```text
0, 1, 2, ..., 20, 24, "+", "×"
```

图片、传统麻将图案、展示文本、规则库对象都应通过适配器转换，不应污染牌局状态。

### ❌ 黑箱化规则入口（判胡与计番已接入，其他规则入口尚未迁移）

不要让游戏状态机直接依赖番种文件或具体规则类。建议建立 `RuleAdapter`，让牌局引擎只调用稳定接口。

当前已经实现并接入 ActionOption/GameState 的稳定接口：

```text
evaluate_win(hand_snapshot, win_context) -> WinResult
```

`HandSnapshot` 只包含有序手牌牌值和副露，不包含 UI 自定义分组。`WinResult` 是不可变、可序列化的纯数据，现有计算器对象和 `FanResults` 不会泄露给牌局状态机。

后续建议继续扩展：

```text
can_win(hand_snapshot, win_context) -> WinCheckResult
calculate_fan(hand_snapshot, win_context) -> FanResult
get_ready_tiles(hand_snapshot, visible_tiles) -> ReadyResult
can_chi(hand_snapshot, discarded_tile, from_player) -> ActionCheckResult
can_peng(hand_snapshot, discarded_tile) -> ActionCheckResult
can_gang(hand_snapshot, tile, gang_type) -> ActionCheckResult
```

其中 `win_context` 至少应包含：

- 胡牌玩家。
- 胡牌牌。
- 胡牌方式：自摸、点胡、抢杠；天胡作为独立计番条件。
- 是否有吃、碰、明杠、暗杠、加杠。
- 当前可见牌信息。
- 出牌者，如果是点胡或抢杠。

### ✅ 天胡判定与付款

胡牌者本局尚未打出过任何一张牌时，自动计天胡 3 番，不限庄家，也不要求其他玩家没有出过牌。判定使用本局完整 `discard_history`，已被吃、碰、杠拿走的弃牌仍算本人出过牌；开始下一局后重新判断。

`WinContext.is_heavenly` 独立于 `win_method`，胡牌提示与最终计番使用相同条件。天胡点胡由点炮者付款，天胡自摸仍由其余三家分别付款，抢杠仍由加杠者付款；其他番种和 6 番封顶沿用原规则。计算器保留旧的显式“天胡”输入兼容性。

### ✅ 判胡与计番黑箱返回格式

计番黑箱返回给牌局引擎的结果应当稳定，不暴露内部实现细节。

建议格式：

```text
can_win: true | false
is_winning_shape: true | false
win_type: string | null
groups: grouped tiles
total_fan: number
starting_fan: number
capped_fan: number
base_payment: number
fan_items: list
can_start: true | false
ruleset_id: string
error: string | null
```

牌局引擎可以展示 `fan_items`，但不应根据具体番种名称改流程。

### ❌ 合同测试（判胡与计番合同已完成，其他规则入口尚未覆盖）

第三阶段前只需要少量合同测试，不需要重新测试所有番种。

重点测试：

- ✅ `evaluate_win` 能区分牌型不成立和番数不足。
- ✅ 最高番胡法、番种、番数、封顶番和单个付款人的基础赔付可稳定序列化。
- ✅ GameState 可以注入替代 RuleAdapter，不直接依赖具体判胡类。
- ❌ `get_ready_tiles` 的稳定 Adapter 入口。（现有计算器有听牌能力，尚未迁移到 RuleAdapter）
- ❌ 吃碰杠等后续 adapter 输入输出格式稳定。
- ✅ 规则黑箱内部替换后，现有 ActionOption 和 GameState 测试不需要改写。

## 核心模块设计

### ✅ TileWall

牌山对象，只负责牌池和摸牌。

职责：

- 按规则牌池生成完整牌堆。
- 洗牌。
- 摸一张牌。
- 查询剩余张数。
- 判断是否还能摸牌。

不负责：

- 判断胡牌。
- 判断动作是否合法。
- 记录玩家手牌。
- 处理结算。

### ✅ PlayerState

玩家状态对象。

建议字段：

```text
player_id
seat
name
is_human
is_dealer
hand_tiles
melded_groups
discarded_tiles
score
is_ready
is_winner
is_out
win_order
win_settlement_id
```

后续可以扩展：

- AI 难度。
- 是否托管。
- 操作计时器。
- 玩家头像和展示名。

### ✅ GameConfig

牌局创建后保持不变的模式配置：

```text
mode: NORMAL | BLOOD_BATTLE
allow_multiple_hu: bool
```

`allow_multiple_hu = false` 时，同一张弃牌存在多个合法胡牌声明，只保留沿点炮者下家方向距离最近的赢家。开启时全部合法胡牌声明生效。血战模式的详细后续行动规则见 `blood_battle_design.md`。

当前 `simulator/config.py` 已实现不可变的 `GameConfig(allow_multiple_hu=True, mode=GameMode.NORMAL)`，可以通过 `GameState.new_game(config=...)` 注入。普通模式仍为默认值，血战用 `GameMode.BLOOD_BATTLE`；房间每局进行时锁定配置，安排下一局时可修改，确认开局后应用新的规则、计时及内置结算器。

### ❌ GameState（部分实现）

整局游戏的核心状态。

建议字段：

```text
game_id
round_id
players
dealer_seat
current_turn_seat
wall
phase
last_discard
pending_responses
last_action
winner_results
discard_history
event_log
```

`phase` 建议先使用有限状态：

```text
INIT
DEALING
WAITING_DRAW
WAITING_TURN_ACTION
WAITING_RESPONSE
WAITING_ACTION_DETAIL
WAITING_ROB_KONG
RESOLVING_ACTION
SETTLING
ROUND_ENDED
```

### ✅ Action

动作是玩家对牌局提交的意图。

第一版建议支持：

```text
DRAW
DISCARD
CHI
PENG
MING_GANG
AN_GANG
JIA_GANG
HU
PASS
```

每个动作至少包含：

```text
action_id
player_id
action_type
tiles
target_tile
source_player_id
metadata
```

动作不应该直接修改状态，而应交给 `GameEngine` 验证和执行。

### ✅ LegalActionService

合法动作枚举器。

职责：

- 在当前状态下列出某玩家能做什么。
- 给前端或 AI 提供可选动作。
- 降低前端误操作概率。

示例：

```text
get_legal_actions(game_state, player_id) -> list[ActionOption]
```

### ✅ ActionResolver

响应和优先级裁决器。

别人打出一张牌后，系统进入 `WAITING_RESPONSE`。所有相关玩家可以提交 `HU`、`MING_GANG`、`PENG`、`CHI` 或 `PASS`。

裁决规则：

1. 胡牌优先于所有动作。
2. 是否允许多人同时胡牌由 `GameConfig.allow_multiple_hu` 决定。
3. 杠优先于碰。
4. 碰优先于吃。
5. 吃只能由下家响应，血战中跳过已经退出的玩家。
6. 多个碰或杠同时发生时，按规则中“逆时针离出牌家最近者”优先。

### ✅ ScoringAdapter（普通与血战）

结算适配层。

职责：

- 根据计番黑箱返回结果计算玩家筹码变化。
- 处理自摸、点胡、抢杠。
- 处理诈胡。
- 处理一炮多响。
- 处理血战中已经退出的玩家和当前有效付款人集合。

第一版可以只支持正常胡牌和流局，诈胡可先记录为待实现。

代码中已经定义不可变的 `SettlementContext`、`SettlementEvent`、`RoundResult`、`ScoreDelta`、`PlayerScore` 和 `ScoringAdapter` Protocol。默认 `NormalScoringAdapter` 已接入 GameState，并支持注入其他结算器。

- ✅ 点胡由点炮者支付一份 `base_payment`。
- ✅ 普通模式自摸由其他三家各支付一份 `base_payment`，血战只由尚未胡牌的对手付款。
- ✅ 一炮多响为每位赢家生成独立的 `SettlementEvent`，点炮者分别付款。
- ✅ 抢杠由加杠者向每位赢家付款；保留原碰组，不完成加杠或补牌。
- ✅ 普通流局生成空赢家结果；血战流局保留此前全部赢家和即时分数，不产生额外赔付。
- ✅ 结算器直接消费 RuleAdapter 的基础赔付，不重复计算番数或硬编码番种。
- ✅ 更新分数前先验证全部赢家的结算记录和收支平衡；结算失败不会只更新部分玩家的分数。
- ✅ `BloodBattleScoringAdapter` 支持 1～3 位有效对手，退出后不再收付款。
- ❌ 诈胡赔付尚未实现。

## 一局游戏流程

### ✅ 开局

1. 创建 `GameState`。
2. 创建四个 `PlayerState`。
3. 创建并洗牌 `TileWall`。
4. 确定庄家。
5. 发牌。
6. 设置当前行动玩家为庄家。
7. 进入 `WAITING_DRAW` 或庄家起手行动阶段。

### ✅ 普通回合（自摸、出牌和杠牌）

1. ✅ 当前玩家摸牌。
2. ✅ 系统检查自摸、暗杠、加杠等可选动作。
3. ✅ 玩家选择出牌、暗杠、加杠、自摸或显式 PASS。
4. ✅ 如果自摸，进入结算并结束普通牌局，不再创建 t1 或 t2。
5. ✅ 暗杠直接补牌并刷新 t3。加杠只在有人能抢时进入 t4：有人胡则结算；全部过则完成加杠、补牌并恢复原 t3 剩余时间；无人能抢则照常刷新 t3。
6. ✅ 如果打牌，进入响应窗口。

### ✅ 响应窗口

1. ✅ 其他玩家根据最近弃牌获得合法响应动作。
2. ✅ 玩家提交响应或超时视为 `PASS`。
3. ✅ `ActionResolver` 裁决最终动作。
4. ✅ 如果有人胡牌，重新调用 RuleAdapter 取得最高番结果，经 ScoringAdapter 更新分数后生成 RoundResult。
5. ✅ 如果有人杠、碰、吃，移动牌到对应明牌区，并由响应者继续打牌。（明杠先补摸一张）
6. ✅ 如果无人响应，轮到下一位玩家摸牌。

### ✅ 结束与下一局（普通模式后端）

一局结束条件：

- 普通模式有玩家胡牌。
- 血战模式累计三位玩家胡牌。
- 牌山摸空。（普通模式流局；血战模式保留此前即时结算后结束）
- 出现诈胡等异常结算。

结束后生成 `RoundResult`：

```text
round_id
end_reason
dealer_seat
next_dealer_seat
winner_ids
settlement_events
score_deltas
final_scores
```

`settlement_events` 为每位赢家保留胡牌方式、胡牌牌张、最高番牌组、番种明细、番数、封顶番和实际赔付。`score_deltas` 是整局四位玩家的分数变化汇总，`final_scores` 是累计分数。弃牌、手牌状态和事件日志继续从 GameState 快照读取。

`SETTLING` 是同步执行的内部阶段，不等待 UI。计算结束即进入 `ROUND_ENDED`，公开快照包含 `round_result`，前端可在这里展示结算。

血战模式额外汇总每次即时结算。流局不产生新的分数变化；如果血战中已经有人胡过，之前完成的结算不回滚。

结算完成后停留在 `ROUND_ENDED` 供玩家查看。玩家手动开始下一局时，保留累计分数、创建新 `round_id`，并令：

```text
next_dealer_seat = (dealer_seat + 1) % 4
```

庄家就是起始玩家，无论流局、庄家是否获胜或采用哪种模式，每局都固定轮换一次。

后端入口为 `start_next_round(rng=..., source_round_id=...)`；只允许在 `ROUND_ENDED` 调用。它保留累计分数、玩家身份、配置和 Adapter，清空上一局手牌、分组、副露、弃牌、计时和临时动作，然后重新洗牌、发牌并进入 `WAITING_DRAW`。可传旧 `round_id` 防止过期的重开请求。

## ❌ AI 设计边界

第三阶段可以先只做极简 AI，不需要智能。

第一版 AI 策略：

1. 能胡则胡。
2. 能杠可按固定概率或固定规则选择。
3. 能碰可按固定概率选择。
4. 能吃可按固定概率选择。
5. 否则随机打一张牌。

AI 不应直接修改 `GameState`，只应读取合法动作列表并返回一个动作。

## ❌ 前端接口准备（公开快照与单人本地接口已实现，正式四人接口待扩展）

虽然第三阶段不做完整前端，但状态快照要提前考虑。

建议对前端暴露：

```text
public_game_state
current_player_view
legal_actions
last_event
round_result
```

注意：

- 真人玩家只能看到自己的手牌。
- AI 手牌在正式前端里应隐藏。
- 弃牌、明牌、牌山剩余数、结算结果可以公开。
- 调试模式可以暴露全部手牌。
- ✅ 倒计时只向对应玩家本人返回；调试公开手牌不会同时公开别人的 deadline。
- ✅ t4 只向有抢杠资格的人提供胡/过选项和倒计时。加杠者不能操作，只可看到自己的暂停剩余时间。

以上为参赛者及当前房间 UI 的边界。多人房间的上帝观众已可只读看四家手牌与倒计时，但不能看未裁决意向或进行中的牌山；角色实现和公网前待办见 `multiplayer_design.md`。

### 当前牌桌与运行入口

旧单人试牌、四人开发者及存档调试入口已于 2026-10-26 移除，历史设计见 `ui_prototype_design.md`。现用入口为 `multiplayer.web` 真人房间和 `multiplayer.bot_play` 人机试玩，共用 `UI/static/` 牌桌。

- ✅ 四席牌桌、实体牌选择、拖动/分组、吃碰杠胡过、结算与下一局。
- ✅ 每局开局前配置模式、多响及计时，本局中锁定，局间可修改；默认 t1/t2/t4 为 5 秒、t3 为 30 秒，计时配置位于 `multiplayer/settings.py`。
- ✅ 跨组移动失败原子保证及旧操作窗口校验，服务端定期推进计时。
- ✅ 房间和人机运行层复用规则引擎，人机接入保留真人命令合同。
- ✅ 核心快照与 SQLite 存储能力保留，当前两个服务每次开服重置，不载入旧存档。

个人断线、暂停与默认托管见 `hosting_design.md`；策略 Bot 与训练见 `bot_design.md`。血战已接入房间模式选择、即时记录和已胡只读状态，血战专用策略训练暂缓。Access、Tunnel/公网和完整回放仍未完成。

## 暂缓事项

以下内容不建议放进第三阶段第一版：

- 真人联机。
- 账号系统。
- 排行榜。
- 复杂 AI。
- 教程关卡。
- 回放播放器。
- 观战。
- 移动端适配。
- 精美动画。

这些都应该在可完整运行单局后再做。

## 建议实现顺序

1. ✅ 定义核心数据结构：`TileWall`、`PlayerState`、`GameState`、`Action`。
2. ✅ 定义 `RuleAdapter`、`ScoringAdapter` 和结算结果接口。
3. ✅ 实现开局、洗牌、发牌。
4. ✅ 实现摸牌和打牌。
5. ✅ 实现合法动作枚举。
6. ✅ 实现响应窗口。
7. ✅ 实现动作优先级裁决。
8. ✅ 接入胡牌和计番黑箱，普通点胡、自摸结算均通过 RuleAdapter。
9. ✅ 生成 SettlementEvent 和 RoundResult，并在公开快照中提供结算明细。
10. ✅ 接入普通/血战模式和退出玩家后的继续流程。
11. ✅ 实现手动开始下一局和庄家固定轮换。
12. ✅ 用确定牌山从发牌、摸牌、自摸跑到结算，并测试点胡、流局和下一局。
13. ❌ 加入极简 AI。

## 成功标准

第三阶段完成的判断标准：

- ✅ 普通模式可以从开局推进到胡牌或流局并生成结算结果。
- ✅ 每一步状态都可序列化。（当前已实现的状态）
- ❌ 所有玩家动作都经过合法性验证。
- ✅ 普通胡牌时通过规则黑箱计算结果。
- ✅ 游戏引擎不依赖具体番种名称。（当前判胡与计番路径）
- ✅ 普通模式支持替换规则和结算 Adapter，修改番种或付款计算不需要改牌局状态机。（已验证替代结算器与错误结果校验）
