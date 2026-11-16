# 手牌身份、排序与动作选牌设计

玩家手牌以一条有序列表展示，副露单独展示。玩家可以拖动实体牌调整手牌顺序；整理不消耗牌局行动，也不暂停或重置计时。

## 稳定实体身份

`simulator/hand_state.py` 只定义 `HandTile`：每张牌包含稳定的 `tile_id` 和牌值 `value`。同值牌拥有不同 ID；拖动不会改变 ID，出牌和动作执行按 ID 移除具体牌。

`PlayerState.hand_tiles` 与 `hand_tile_ids` 一一对应，顺序就是视觉顺序。`hand_tile_records()` 返回按此顺序排列的实体牌记录。直接重设整副 `hand_tiles` 时重新生成实体 ID；普通拖动、摸牌和移除保留其余实体身份。

## 拖动排序

- 所有未胡玩家在 t1、t2、t3、t4 均可整理自己的手牌，包括自己选择吃法、已提交响应及声明加杠后。
- `move_hand_tile_by_id(player_id, tile_id, to_index)` 移动指定实体牌。目标索引是移除原牌后的插入位置，可从 0 到剩余牌数。
- 单机 `move` 请求携带本局 `round_id`、本人 `hand_revision`、`tile_id` 和 `index`；旧局、旧手牌版本和非法位置均拒绝，失败不改变手牌或记录成功事件。
- 前端通过 `ui.hand_ordering_allowed` 判断能否拖动。拖动期间继续接收状态；本人手牌未变的阶段推进保留拖动，本人手牌改变、换局、退出或停止整理时取消旧拖动。
- 左键选择出牌或手动吃牌；拖动结束抑制随后的误点击。出牌高亮随出牌窗口清空，手动吃牌选择按 detail ID 管理，拖动后仍跟随原实体。
- 结算后、血战已胡退出及非行动阶段禁止整理。

## 吃碰杠的实体选牌

`simulator/tile_selection.py` 的 `select_tiles()` 统一负责实体绑定：

1. 手动吃牌提交明确的实体 ID 时，验证数量、牌值、归属和无重复，使用这些原始实体；返回 `selection_source="explicit"`。
2. 列表吃法、碰、明杠、暗杠和加杠按执行时当前手牌顺序从左到右选取满足需求的牌；返回 `selection_source="hand_order"`。

选牌结果只包含 `tile_ids` 和 `selection_source`。动作需要的牌值与数量来自合法动作结果，选牌器不推断麻将张数。手牌不足时拒绝，选牌本身不修改手牌；执行器验证后按 ID 移除并创建或升级副露。

读取最新手牌顺序、选定实体和执行移除在牌桌锁内完成。尚未提交的拖动不改变自动选牌。加杠进入 t4 前已绑定具体实体，t4 中拖动不能替换或取消声明使用的牌。

## 摸牌、超时与判胡

新摸牌追加在手牌最右侧，`drawn_tile_id` 始终追踪真正摸到的实体。拖动后自摸仍以该牌作为胡牌牌张。t3 超时先按默认过提示处理，再打出当前手牌最右侧实体；未拖动时通常是刚摸到的牌。

排序只改变视觉顺序及自动选取同值实体的先后，不改变判胡、听牌或计番含义。`calculator_base`、`fan_calculator` 中的 `hand_groups` 表示牌型分解，继续保留；它们与玩家整理操作无关。

## 公开状态与保存恢复

牌主的公开状态包含 `hand_tiles` 和有序 `hand_tile_records`；对手进行中的暗牌和 ID 不公开。副露继续使用 `melded_groups` 与 `display_tiles` 展示。

私有快照格式为 v3，保存手牌顺序、稳定实体 ID、行动窗口、计时与结算。v1/v2 快照明确拒绝恢复；单机入口不自动读取旧游玩存档。详见 [保存恢复](state_recovery_design.md)。

## 验证入口

实体身份、移动原子性、跨阶段整理和自动选牌见 `test/test_hand_state.py`、`test/test_ui_hand_commands.py`、`test/test_tile_selection.py`；动作、抢杠和快照测试覆盖实体在执行中的连续性。`singleplayer/test/test_singleplayer.py` 验证命令版本和 HTTP 合同；桌面自检与内嵌窗口检查验证实际拖动、出牌及 Bot 轮转。
