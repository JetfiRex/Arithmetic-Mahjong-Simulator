"use strict";

const $ = (id) => document.getElementById(id);
const singlePlayer = window.singlePlayer;
const LABEL = { CHI: "吃", PENG: "碰", MING_GANG: "杠", AN_GANG: "暗杠", JIA_GANG: "加杠", HU: "胡", PASS: "过" };
const STAGES = { LOBBY: "等待开局", HAND_PREPARATION: "开局理牌", BOT_TURN: "其他玩家出牌回合", READY_SOURCE: "等待来牌", OWN_TURN: "我的出牌回合", WAITING_DRAW: "等待摸牌", WAITING_RESPONSE: "来牌响应", WAITING_ACTION_DETAIL: "选择吃法", WAITING_ROB_KONG: "等待其他玩家响应", ROUND_ENDED: "本局结束" };
const TIMER_FIELDS = [
  ["discard_response_seconds", "RESPONSE_WINDOW", "吃碰杠胡响应", 5],
  ["action_detail_seconds", "DETAIL_SELECTION", "选择吃法", 5],
  ["turn_action_seconds", "TURN_DECISION", "摸牌后出牌", 30],
  ["rob_kong_seconds", "ROB_KONG_RESPONSE", "抢杠响应", 5],
];
const TIMER_LABELS = Object.fromEntries(TIMER_FIELDS.flatMap(([, kind, label], index) =>
  [[kind, label], [`t${index + 1}`, label]]));
const VALUES = [...Array(21).keys(), 24, "+", "×"];
let state, busy = false, polling = false, numberView = false;
let selected = null, chiSelection = new Set();
let deckSelection = null, dragging = null, editorKind = null, shownResult = null;
let receivedAt = 0, toastTimer, lastWindow = null, lastChiWindow = null;
let suppressedHandClick = null;
let settingsKey = null, setupShownFor = null;
const renderedSections = new Map();

function renderSection(name, inputs, draw) {
  const key = JSON.stringify(inputs);
  if (renderedSections.get(name) === key) return;
  draw();
  renderedSections.set(name, key);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/assets/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

function tile(value, interactive = false) {
  const node = element(interactive ? "button" : "span", "tile");
  node.dataset.value = value;
  if (typeof value === "string") node.classList.add("operator");
  if (numberView) node.classList.add("number-view");
  const img = element("img");
  img.src = `/tiles/${value === "+" ? "plus" : value === "×" ? "mult" : value}.png`;
  img.alt = "";
  img.draggable = false;
  node.append(img, element("span", "tile-value", value));
  node.setAttribute("aria-label", `${value} 牌`);
  if (interactive) node.type = "button";
  return node;
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
}

function ui() {
  const value = state.ui || state.practice;
  return value;
}
function viewerId() { return singlePlayer ? state.viewer_player_id : "p0"; }
function me() {
  const player = state.players.find((p) => p.player_id === viewerId()) || state.players[0];
  return { hand_tile_records: [], ...player };
}
function stage() { return ui().stage; }
function manualChi() {
  return state.pending_action_detail?.player_id === viewerId()
    && state.pending_action_detail.selection_mode === "manual";
}
function handLayoutKey() {
  return JSON.stringify([state.round_id, state.hand_revision, me().hand_tile_records]);
}

// Actions carry their window; layout carries its original round/own-hand version.
async function send(type, data = {}) {
  if (busy || !state) return false;
  busy = true;
  document.body.classList.add("working");
  try {
    const response = singlePlayer ? await singlePlayer.send(type, data) : await fetch("/api/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, revision: state.revision, turn_window_id: state.turn_window_id, ...data }),
    });
    const result = await response.json();
    if (result.state) accept(result.state);
    if (!response.ok) throw new Error(result.error || "操作失败");
    return true;
  } catch (error) {
    if ($("setup-dialog").open) $("setup-error").textContent = error.message;
    toast(error.message || "连接失败");
    return false;
  } finally {
    busy = false;
    document.body.classList.remove("working");
    window.dispatchEvent(new Event("table-action-idle"));
  }
}

async function poll() {
  if (busy || polling || dragging?.kind === "deck") return;
  polling = true;
  try {
    const response = singlePlayer ? await singlePlayer.poll() : await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("离线");
    const next = await response.json();
    if (!busy && dragging?.kind !== "deck") accept(next);
    $("connection-dot").classList.remove("offline");
  } catch {
    $("connection-dot").classList.add("offline");
  } finally { polling = false; }
}

function accept(next) {
  if (singlePlayer && (state?.instance_id !== next.instance_id)) {
    state = null;
    renderedSections.clear();
  }
  if (state && next.revision < state.revision) return;
  if (singlePlayer && state?.round_id !== next.round_id && $("result-dialog").open) $("result-dialog").close();
  const changed = !state || next.revision !== state.revision;
  state = next;
  receivedAt = performance.now();
  if (changed) {
    const window = `${state.round_id}:${state.turn_window_id}:${state.pending_action_detail?.detail_id || ""}`;
    if (lastWindow !== window) {
      selected = null;
      lastWindow = window;
    }
    const detail = state.pending_action_detail;
    const chiWindow = detail?.player_id === viewerId() ? `${state.round_id}:${detail.detail_id}` : null;
    if (lastChiWindow !== chiWindow) { chiSelection.clear(); lastChiWindow = chiWindow; }
    if (ui().recovery_pending) { selected = null; chiSelection.clear(); }
    if (dragging?.kind === "hand" && (!ui().hand_ordering_allowed || dragging.layoutKey !== handLayoutKey())) endDrag(false);
    const ids = new Set(me().hand_tile_records.map((t) => t.tile_id));
    if (!ids.has(selected)) selected = null;
    chiSelection = new Set([...chiSelection].filter((id) => ids.has(id)));
    render();
  }
  renderTimer();
}

function render() {
  if (singlePlayer && !singlePlayer.isTable()) {
    singlePlayer.render();
    return;
  }
  const practice = ui();
  $("round-label").textContent = practice.started === false ? "等待开局" : practice.mode === "four" ? `第 ${practice.round_number} 局` : `第 ${practice.round_number} 次试牌`;
  $("clock-label").textContent = practice.timeout_settings ? Object.values(practice.timeout_settings).some((v) => v !== null) ? "计时已开启" : "不计时" : practice.scene === "timer" ? "计时体验" : "无计时";
  $("mode-label").textContent = practice.mode === "four" ? "四人开发者局" : "单人试牌";
  if (!singlePlayer) renderSection("table-mode", practice.mode, () => {
    $("table-mode").replaceChildren(document.createTextNode(practice.mode === "four" ? "FOUR PLAYERS" : "SOLITAIRE"), element("span", "", practice.mode === "four" ? "四人开发者局" : "单人试牌台"));
  });
  if (!$("scene-select").options.length) {
    for (const [value, label] of Object.entries(practice.scenes)) {
      const option = element("option", "", label); option.value = value; $("scene-select").append(option);
    }
  }
  $("scene-select").value = practice.scene;
  $("scene-select").parentElement.hidden = practice.mode === "four";
  $("scene-select").disabled = practice.settings_locked === true;
  $("wall-counter").textContent = `牌山 ${state.wall.remaining_count}`;
  $("stage-label").textContent = stage() === "BOT_TURN" ? `${state.players[state.current_turn_seat].name} · 出牌` : stage() === "WAITING_ROB_KONG" && state.rob_kong_window ? "抢杠响应" : STAGES[stage()] || "处理中";
  $("hand-count").textContent = `${me().hand_count} 张`;
  $("edit-hand").disabled = !practice.editing_allowed;
  $("edit-wall").disabled = !practice.editing_allowed;
  $("deck-add").disabled = !practice.editing_allowed;
  $("show-result").hidden = !state.round_result;
  renderHand();
  renderMelds();
  renderSection("table", [numberView, ui().mode, stage(), ui().drawn_tile_id,
    state.rob_kong_window, state.last_discard, me().discarded_tiles,
    me().hand_tile_records.find((t) => t.tile_id === ui().drawn_tile_id),
    ui().mode === "four" ? null : state.discard_history], renderTable);
  renderDeck();
  renderActions();
  renderActivity();
  renderOpponents();
  renderSection("flow", [ui().started, visiblePhase(), ui().phase_history], renderFlow);
  renderSettings();
  renderRecovery();
  if (singlePlayer) singlePlayer.render();
  if (!practice.recovery_pending && state.round_result && shownResult !== state.round_id
      && (!singlePlayer || singlePlayer.isTable() && state.table?.phase === "REVIEWING")) {
    shownResult = state.round_id;
    showResult();
  }
}

function renderHand() {
  // Keep the native drag DOM alive while other players' actions advance.
  if (dragging?.kind === "hand") return;
  const root = $("hand-regions");
  const scroll = $("hand-scroll").scrollLeft;
  let zone = root.querySelector(".hand-region");
  if (!zone) {
    const meldArea = element("div", "meld-area"); meldArea.id = "meld-area";
    zone = element("div", "hand-region");
    zone.setAttribute("aria-label", "手牌");
    root.replaceChildren(meldArea, zone);
  }
  const existing = new Map([...zone.children].map((node) => [node.dataset.tileId, node]));
  const wanted = new Set(me().hand_tile_records.map((record) => record.tile_id));
  for (const [id, node] of existing) if (!wanted.has(id)) node.remove();
  let position = 0;
  for (const { tile_id: id, value } of me().hand_tile_records) {
    let node = existing.get(id);
    if (node && node.dataset.value !== String(value)) { node.remove(); node = null; }
    const created = !node;
    if (created) node = tile(value, true);
    const disabled = !!me().is_out || !!ui().recovery_pending;
    if (node.disabled !== disabled) node.disabled = disabled;
    node.classList.toggle("number-view", numberView);
    node.classList.toggle("selected", selected === id || chiSelection.has(id));
    if (node.draggable !== !!ui().hand_ordering_allowed) node.draggable = !!ui().hand_ordering_allowed;
    if (zone.children[position] !== node) zone.insertBefore(node, zone.children[position] || null);
    position++;
    if (!created) continue;
    node.dataset.tileId = id;
    node.title = `${value}`;
    // An intentional new press must not be swallowed by drag-click suppression.
    node.onpointerdown = () => { suppressedHandClick = null; };
    node.onclick = () => {
      if (suppressedHandClick?.id === id && performance.now() < suppressedHandClick.until) return;
      if (manualChi()) {
        if (chiSelection.has(id)) chiSelection.delete(id);
        else if (chiSelection.size < 3) chiSelection.add(id);
        else toast("最多选择 3 张牌");
      } else if (stage() === "OWN_TURN") selected = selected === id ? null : id;
      renderHand(); renderActions();
    };
    node.ondragstart = (event) => {
      if (!ui().hand_ordering_allowed || busy) { event.preventDefault(); return; }
      dragging = { kind: "hand", id, layoutKey: handLayoutKey(),
        origin: { round_id: state.round_id, hand_revision: state.hand_revision }, geometry: null };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", id);
      node.classList.add("dragging");
    };
    node.ondragend = () => endDrag();
  }
  zone.ondragover = (event) => {
    if (dragging?.kind !== "hand" || !ui().hand_ordering_allowed) return;
    event.preventDefault();
    if (!dragging.geometry) {
      const targets = [...zone.querySelectorAll("[data-tile-id]")].filter((n) => n.dataset.tileId !== dragging.id);
      dragging.geometry = targets.map((node) => {
        const rect = node.getBoundingClientRect();
        return { middle: (rect.left + rect.right) / 2, left: node.offsetLeft, width: node.offsetWidth };
      });
    }
    const before = dragging.geometry.find((target) => event.clientX < target.middle);
    const last = dragging.geometry.at(-1);
    const left = `${before ? before.left - 3 : (last?.left || 0) + (last?.width || 4) + 2}px`;
    let indicator = zone.querySelector(".drop-slot");
    if (!indicator) { indicator = element("span", "drop-slot"); zone.append(indicator); }
    zone.classList.add("drop-active");
    if (indicator.style.left !== left) indicator.style.left = left;
  };
  zone.ondrop = (event) => {
    if (dragging?.kind !== "hand" || !ui().hand_ordering_allowed) return;
    event.preventDefault();
    const targets = [...zone.querySelectorAll("[data-tile-id]")].filter((n) => n.dataset.tileId !== dragging.id);
    let index = targets.findIndex((n) => event.clientX < n.getBoundingClientRect().left + n.offsetWidth / 2);
    if (index < 0) index = targets.length;
    const { id, origin } = dragging; endDrag(false);
    send("move", { tile_id: id, index, ...origin });
  };
  root.ondragover = (event) => { if (event.target === root) zone.ondragover(event); };
  root.ondrop = (event) => { if (event.target === root) zone.ondrop(event); };
  $("hand-scroll").onscroll = () => { if (dragging?.kind === "hand") dragging.geometry = null; };
  $("hand-scroll").scrollLeft = scroll;
}

function clearDrop() {
  document.querySelectorAll(".drop-slot").forEach((n) => n.remove());
  document.querySelectorAll(".drop-active,.drop-target").forEach((n) => n.classList.remove("drop-active", "drop-target"));
}
function endDrag(refresh = true) {
  if (dragging?.kind === "hand") suppressedHandClick = { id: dragging.id, until: performance.now() + 300 };
  dragging = null;
  clearDrop(); document.querySelectorAll(".dragging").forEach((n) => n.classList.remove("dragging"));
  if (refresh && state) { renderHand(); renderMelds(); renderActions(); }
}

function renderMelds() {
  if (dragging?.kind === "hand") return;
  renderSection("melds", [state.round_id, numberView, me().melded_groups], () => {
    const root = $("meld-area"); root.replaceChildren();
    for (const meld of me().melded_groups) {
      const group = element("div", "meld"); group.title = meld.group_type;
      const faces = meld.display_tiles || meld.tiles.map((value) => ({ value, orientation: "upright" }));
      for (const face of faces) {
        const node = tile(face.value);
        if (face.orientation === "sideways") node.classList.add("sideways");
        if (face.orientation === "back") { node.classList.add("back"); node.setAttribute("aria-label", "背面牌"); }
        group.append(node);
      }
      root.append(group);
    }
  });
}

function renderTable() {
  $("incoming-tile").replaceChildren();
  const response = ["WAITING_RESPONSE", "WAITING_ACTION_DETAIL"].includes(state.phase);
  const rob = state.rob_kong_window;
  const drawn = me().hand_tile_records.find((t) => t.tile_id === ui().drawn_tile_id);
  const value = rob ? rob.tile : response ? state.last_discard?.tile : stage() === "OWN_TURN" ? drawn?.value : null;
  if (value !== null && value !== undefined) $("incoming-tile").append(tile(value));
  $("incoming-caption").textContent = rob ? `${state.players.find((p) => p.player_id === rob.source_player_id)?.name}加杠` : response ? "来牌" : drawn ? "本次摸牌" : stage() === "OWN_TURN" ? "待出牌" : "等待来牌";
  const river = $("discard-river"); river.replaceChildren();
  const four = ui().mode === "four";
  $("discard-label").textContent = four ? "我的弃牌" : "弃牌";
  river.setAttribute("aria-label", four ? "我的弃牌" : "弃牌");
  if (four) {
    river.dataset.playerId = me().player_id;
    appendDiscards(river, me());
    $("discard-count").textContent = `${me().discarded_tiles.length} 张`;
    return;
  }
  delete river.dataset.playerId;
  const visibleDiscards = state.players.flatMap((p) => p.discarded_tiles);
  // History also contains claimed tiles; only render discards still in the river.
  const counts = new Map(); visibleDiscards.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
  const history = state.discard_history.slice().reverse().filter((record) => {
    if (!(counts.get(record.tile) > 0)) return false;
    counts.set(record.tile, counts.get(record.tile) - 1); return true;
  }).reverse();
  for (const record of history) {
    const node = tile(record.tile); node.title = `${state.players.find((p) => p.player_id === record.player_id)?.name}的弃牌`; river.append(node);
  }
  $("discard-count").textContent = `${history.length} 张`;
}

function appendDiscards(root, player) {
  for (const value of player.discarded_tiles) {
    const node = tile(value);
    node.title = `${player.name}的弃牌`;
    root.append(node);
  }
}

function action(label, handler, className = "", disabled = false, symbol = null) {
  const node = element("button", `action-button ${className}`, label);
  if (symbol) node.append(icon(symbol));
  node.disabled = disabled; node.onclick = handler; $("actions").append(node);
}

function renderActions() {
  renderSection("actions", [state.round_id, stage(), selected, [...chiSelection], numberView,
    ui().recovery_pending, state.table?.phase, state.table?.paused, me().win_order,
    state.response_window, state.pending_action_detail, state.rob_kong_window,
    state.turn_options_by_player, state.turn_hu_prompt_passed, state.turn_gang_prompt_passed,
    state.current_turn_seat, state.round_result?.end_reason], () => {
    $("actions").replaceChildren(); $("chi-options").replaceChildren();
    if (singlePlayer?.renderActions()) return;
    const status = $("action-status");
    if (ui().recovery_pending) {
      status.textContent = "对局已暂停";
      action("恢复对局", renderRecovery, "primary", false, "arrow-right");
    } else if (stage() === "LOBBY") {
      status.textContent = "等待确认开局设置";
      action("开局设置", openSettings, "primary", false, "arrow-right");
    } else if (stage() === "BOT_TURN") {
      status.textContent = `${state.players[state.current_turn_seat].name}的回合`;
    } else if (stage() === "READY_SOURCE") {
      status.textContent = "来牌待翻开";
      action("翻牌", () => send("reveal"), "primary", false, "arrow-right");
    } else if (stage() === "WAITING_DRAW") {
      status.textContent = "响应已结束";
      action("摸牌", () => send("draw"), "primary", false, "plus");
    } else if (stage() === "WAITING_RESPONSE") {
      const window = state.response_window;
      const submitted = !!window.requests_by_player[viewerId()];
      const options = window.options_by_player[viewerId()] || [];
      status.textContent = submitted ? "已提交，等待响应结束" : options.length ? "选择来牌操作" : "等待响应结束";
      for (const option of options) action(LABEL[option.action_type], () => send("response", { action: option.action_type, window_id: window.window_id }),
        option.action_type === "HU" ? "hu" : option.action_type === "PASS" ? "" : "primary", submitted);
    } else if (stage() === "WAITING_ACTION_DETAIL") {
      const detail = state.pending_action_detail;
      if (manualChi()) {
        status.textContent = `已选 ${chiSelection.size} / 3 张`;
        action("确认吃牌", () => send("chi", { tile_ids: [...chiSelection], detail_id: detail.detail_id }), "primary", chiSelection.size !== 3, "check");
      } else {
        status.textContent = `${detail.payload_options.length} 种吃法`;
        for (const option of detail.payload_options) {
          const choice = element("button", "chi-choice");
          choice.title = `吃 ${option.preview}`; choice.setAttribute("aria-label", choice.title);
          option.display_tiles.forEach((value, index) => {
            const node = tile(value); if (index === option.claimed_display_index) node.classList.add("claimed"); choice.append(node);
          });
          choice.onclick = () => send("chi", { option_id: option.option_id, detail_id: detail.detail_id });
          $("chi-options").append(choice);
        }
      }
      action("过", () => send("chi", { detail_id: detail.detail_id }));
    } else if (stage() === "OWN_TURN") {
      const options = state.turn_options_by_player[viewerId()] || [];
      let blocked = false, hasPrompt = false;
      for (const option of options) {
        const kind = option.action_type;
        if (kind === "HU" && !state.turn_hu_prompt_passed) {
          hasPrompt = true;
          action("胡", () => send("turn_action", { action: kind }), "hu");
        }
        if (["AN_GANG", "JIA_GANG"].includes(kind) && !state.turn_gang_prompt_passed) {
          blocked = hasPrompt = true;
          for (const payload of option.payload_options) action(`${LABEL[kind]} ${payload.tile}`, () => send("turn_action", { action: kind, option_id: payload.option_id }), "gang");
        }
      }
      if (hasPrompt) action("过", () => send("turn_action", { action: "PASS" }));
      status.textContent = blocked ? "有杠牌选项" : selected ? `已选 ${me().hand_tile_records.find((t) => t.tile_id === selected)?.value}` : "我的回合";
      action("出牌", () => send("discard", { tile_id: selected }), "primary", !selected || blocked, "arrow-right");
    } else if (stage() === "WAITING_ROB_KONG" && state.rob_kong_window) {
      status.textContent = "抢杠响应";
      for (const option of state.rob_kong_window.options) action(LABEL[option.action_type], () => send("rob_response", { action: option.action_type, window_id: state.rob_kong_window.window_id }), option.action_type === "HU" ? "hu" : "", state.rob_kong_window.submitted);
    } else if (stage() === "ROUND_ENDED") {
      status.textContent = state.round_result.end_reason === "wall_empty" ? "牌山已空" : "本局已结算";
      action("查看结算", showResult, "primary");
    } else status.textContent = "等待其他玩家响应";
  });
}

function renderTimer() {
  if (singlePlayer && !singlePlayer.isTable()) return;
  if (state?.table?.paused) {
    $("timer").hidden = false;
    const remaining = state.active_timer ? Math.max(0, state.active_timer.deadline_at - state.server_now) : null;
    setTimerText(remaining === null ? "已暂停" : `${Math.ceil(remaining)}s · 已暂停`);
    $("timer").classList.remove("urgent"); return;
  }
  if (state && ui().recovery_pending) {
    const remaining = ui().recovery_remaining_seconds;
    $("timer").hidden = false;
    setTimerText(remaining == null ? "已暂停" : `${Math.ceil(remaining)}s · 暂停`);
    $("timer").classList.remove("urgent");
    return;
  }
  const timer = state?.active_timer;
  const paused = state?.paused_turn_remaining_seconds;
  $("timer").hidden = !timer && paused === null;
  if (!timer && paused !== null && paused !== undefined) { setTimerText(`${Math.ceil(paused)}s · 暂停`); return; }
  if (!timer) return;
  const seconds = Math.max(0, timer.deadline_at - state.server_now - (performance.now() - receivedAt) / 1000);
  const name = timer.kind === "HAND_PREPARATION" ? "理牌" : TIMER_LABELS[timer.kind];
  setTimerText(`${name} · ${Math.ceil(seconds)}s`);
  $("timer").classList.toggle("urgent", seconds <= 5);
}

function setTimerText(text) {
  if ($("timer").textContent !== text) $("timer").textContent = text;
}

function renderOpponents() {
  const root = $("opponent-seats"); root.hidden = ui().mode !== "four";
  if (root.hidden) return;
  for (const offset of [3, 2, 1]) {
    const seat = ((singlePlayer ? state.viewer_seat || 0 : 0) + offset) % 4;
    const player = state.players[seat];
    const active = state.phase === "WAITING_TURN_ACTION" && state.current_turn_seat === seat;
    const reveal = singlePlayer?.currentMode === "teaching" && $("study-reveal").checked && player.hand_tiles;
    renderSection(`opponent-${seat}`, [state.round_id, player, active, numberView, !!reveal], () => {
      const panel = element("div", "opponent-seat");
      panel.classList.toggle("active", active);
      panel.dataset.seat = seat;
      const heading = element("div", "opponent-heading");
      heading.append(element("strong", "", player.name), element("span", "", `${player.is_dealer ? "庄 · " : ""}${player.is_out ? `已胡 · 第${player.win_order}位 · ` : ""}${player.hand_count} 张`));
      const backs = element("div", "opponent-hand");
      if (reveal) {
        backs.classList.add("study-open-hand");
        for (const value of player.hand_tiles) backs.append(tile(value));
      } else for (let i = 0; i < player.hand_count; i++) backs.append(element("span", "mini-back"));
      panel.append(heading, backs, element("span", "opponent-score", `${player.score} 分`));
      if (singlePlayer) {
        const melds = element("div", "opponent-melds");
        for (const meld of player.melded_groups) {
          const group = element("span", "meld");
          for (const face of meld.display_tiles || meld.tiles.map((value) => ({ value }))) {
            const node = tile(face.value);
            if (face.orientation === "back") node.classList.add("back");
            if (face.orientation === "sideways") node.classList.add("sideways");
            group.append(node);
          }
          melds.append(group);
        }
        panel.append(melds);
      }
      const discards = element("div", "opponent-discard-zone");
      const caption = element("div", "zone-heading");
      caption.append(element("span", "", "弃牌"), element("span", "", `${player.discarded_tiles.length} 张`));
      const river = element("div", "discard-river opponent-discards");
      river.dataset.playerId = player.player_id;
      river.setAttribute("aria-label", `${player.name}的弃牌`);
      appendDiscards(river, player);
      discards.append(caption, river);
      panel.append(discards);
      const previous = root.querySelector(`[data-seat="${seat}"]`);
      if (previous) previous.replaceWith(panel);
      else root.insertBefore(panel, root.children[[3, 2, 1].indexOf(offset)] || null);
    });
  }
}

function visiblePhase() {
  if (singlePlayer && state.active_timer?.kind === "ROB_KONG_RESPONSE") return "ROB_KONG_RESPONSE";
  if (state.phase === "WAITING_ROB_KONG") return state.rob_kong_window ? "ROB_KONG_RESPONSE" : "TURN_DECISION";
  return { WAITING_RESPONSE: "RESPONSE_WINDOW", WAITING_ACTION_DETAIL: "DETAIL_SELECTION", WAITING_TURN_ACTION: "TURN_DECISION" }[state.phase];
}

function renderFlow() {
  $("flow-monitor").hidden = !ui().started || stage() === "HAND_PREPARATION";
  $("phase-steps").replaceChildren(); $("phase-history").replaceChildren();
  if (!ui().started) return;
  for (const [, phase, label] of TIMER_FIELDS) {
    const node = element("span", "phase-step", label);
    node.classList.toggle("active", visiblePhase() === phase); $("phase-steps").append(node);
  }
  for (const entry of (ui().phase_history || []).slice(-7)) {
    const name = state.players.find((p) => p.player_id === entry.player_id)?.name;
    $("phase-history").append(element("span", "", `${entry.number}. ${TIMER_LABELS[entry.phase] || entry.phase} · ${name}`));
  }
}

function openSettings() {
  if (singlePlayer) { singlePlayer.openSettings(); return; }
  if (!("started" in ui())) { send("scene", { scene: ui().scene }); return; }
  renderSettings();
  if (!$("setup-dialog").open) $("setup-dialog").showModal();
}

function syncSettingControls() {
  const locked = ui().settings_locked;
  const enabled = $("timers-enabled").checked;
  document.querySelectorAll(".timer-setting").forEach((row) => {
    row.querySelector("[type=checkbox]").disabled = locked || !enabled;
    row.querySelector("[type=number]").disabled = locked || !enabled || !row.querySelector("[type=checkbox]").checked;
  });
  const solo = document.querySelector('[name="game-mode"]:checked').value === "solo";
  $("setup-scene-row").hidden = !solo;
}

function renderSettings() {
  if (singlePlayer) return;
  const practice = ui();
  if (!("started" in practice)) return;
  const key = `${state.game_id}:${practice.settings_locked}:${JSON.stringify(practice.timeout_settings)}`;
  if (key !== settingsKey) {
    settingsKey = key;
    $("setup-error").textContent = "";
    $("timer-settings").replaceChildren();
    for (const [field, phase, label, fallback] of TIMER_FIELDS) {
      const value = practice.timeout_settings[field];
      const row = element("div", "timer-setting"); row.dataset.field = field;
      const toggle = element("input"); toggle.type = "checkbox"; toggle.checked = value !== null; toggle.setAttribute("aria-label", `启用${label}`); toggle.onchange = syncSettingControls;
      const input = element("input"); input.type = "number"; input.min = "1"; input.max = "600"; input.step = "1"; input.value = value ?? fallback; input.setAttribute("aria-label", `${label}秒数`);
      row.append(toggle, element("strong", "", label), input, element("small", "", "秒")); $("timer-settings").append(row);
    }
    $("timers-enabled").checked = Object.values(practice.timeout_settings).some((v) => v !== null);
    $("timers-enabled").disabled = practice.settings_locked;
    document.querySelectorAll('[name="game-mode"]').forEach((input) => { input.checked = input.value === practice.mode; input.disabled = practice.settings_locked; });
    $("setup-scene").replaceChildren();
    for (const [value, label] of Object.entries(practice.scenes)) { const option = element("option", "", label); option.value = value; $("setup-scene").append(option); }
    $("setup-scene").value = practice.scene;
    $("setup-scene").disabled = practice.settings_locked;
    $("start-game").hidden = practice.started;
    $("reset-game").hidden = !practice.started;
    $("setup-status").textContent = practice.started ? "本局计时与模式已锁定" : "开局前可调整";
    syncSettingControls();
  }
  if (!practice.started && setupShownFor !== state.round_id) {
    setupShownFor = state.round_id;
    if (!$("setup-dialog").open) $("setup-dialog").showModal();
  }
}

function renderDeck() {
  if (singlePlayer) return;
  const values = ui().wall_tiles;
  const root = $("deck-grid"), scroll = root.scrollTop; root.replaceChildren();
  if (deckSelection >= values.length) deckSelection = null;
  $("next-tile").replaceChildren();
  if (values.length) $("next-tile").append(tile(values[0]));
  $("deck-remaining").textContent = values.length;
  const editable = ui().editing_allowed;
  $("deck-to-front").disabled = !editable || deckSelection === null || deckSelection === 0;
  $("deck-delete").disabled = !editable || deckSelection === null;
  $("deck-selection").textContent = deckSelection === null ? "未选中牌" : `第 ${deckSelection + 1} 张 · ${values[deckSelection]}`;
  values.forEach((value, index) => {
    const cell = element("button", "deck-cell"); cell.title = `第 ${index + 1} 张：${value}`;
    cell.setAttribute("aria-label", cell.title); cell.classList.toggle("selected", index === deckSelection);
    cell.append(tile(value), element("span", "", String(index + 1).padStart(2, "0")));
    cell.onclick = () => { deckSelection = index; renderDeck(); };
    cell.draggable = editable;
    cell.ondragstart = (event) => {
      dragging = { kind: "deck", index }; event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(index));
    };
    cell.ondragend = endDrag;
    cell.ondragover = (event) => { if (dragging?.kind === "deck") { event.preventDefault(); clearDrop(); cell.classList.add("drop-target"); } };
    cell.ondrop = (event) => {
      if (dragging?.kind !== "deck") return;
      event.preventDefault(); const from = dragging.index; endDrag();
      const tiles = [...values]; const [value] = tiles.splice(from, 1); tiles.splice(index, 0, value);
      deckSelection = index; send("edit_wall", { tiles });
    };
    root.append(cell);
  });
  root.scrollTop = scroll;
}

function renderActivity() {
  const event = state.event_log.at(-1);
  const labels = { tile_drawn: "已摸牌", tile_discarded: "已出牌", hand_tile_moved: "手牌位置已更新", turn_action_passed: "已跳过本次提示", chi_completed: "吃牌完成", peng_completed: "碰牌完成", gang_completed: "杠牌完成", win_settled: "胡牌已结算", round_ended: "本局结束", practice_wall_edited: "牌堆已更新", practice_hand_edited: "手牌已更新", response_window_opened: "来牌响应中", detail_selection_opened: "等待选择吃法" };
  $("activity-text").textContent = event ? labels[event.event_type] || labels[event.type] || STAGES[stage()] : "等待第一张来牌";
}

function openEditor(kind) {
  if (!ui().editing_allowed) return;
  editorKind = kind;
  $("editor-title").textContent = kind === "edit_wall" ? "编辑牌堆" : "编辑手牌";
  $("editor-order").textContent = kind === "edit_wall" ? "摸牌顺序 · 下一张在最前" : "手牌顺序 · 应用后重新生成实体牌 ID";
  $("tile-input").value = JSON.stringify(kind === "edit_wall" ? ui().wall_tiles : me().hand_tiles);
  $("editor-error").textContent = "";
  $("editor-dialog").showModal();
}

function showResult() {
  const finished = Boolean(state.round_result);
  const partial = !finished && state.config?.mode === "blood_battle" && state.settlement_events?.length;
  const result = state.round_result || (partial ? { settlement_events: state.settlement_events,
    score_deltas: state.players.map((player) => ({ player_id: player.player_id,
      delta: state.settlement_events.reduce((total, event) => total + (event.score_deltas.find((item) => item.player_id === player.player_id)?.delta || 0), 0) })) } : null);
  if (!result) return;
  const content = $("result-content"); content.replaceChildren();
  const ownEvent = result.settlement_events.find((e) => e.winner_id === viewerId());
  const events = singlePlayer ? result.settlement_events : ownEvent ? [ownEvent] : [];
  $("result-title").textContent = events.length ? `${events[0].win_result.win_method} · 胡牌` : "流局";
  if (state.config?.mode === "blood_battle") $("result-title").textContent = finished
    ? (result.end_reason === "wall_empty" ? "血战流局 · 保留此前结算" : "血战结束 · 三家已胡")
    : `血战即时结算 · 已胡 ${events.length}/3 家`;
  if (partial) content.append(element("p", "result-method", "血战仍在继续，整局结束后可查看牌山。"));
  for (const event of events) {
    if (singlePlayer) content.append(element("h3", "", `${event.win_order ? `第 ${event.win_order} 位 · ` : ""}${state.players.find((p) => p.player_id === event.winner_id)?.name}`));
    const win = event.win_result;
    const summary = element("div", "result-summary");
    summary.append(element("strong", "result-fan", win.total_fan), element("span", "", "番"));
    const payment = element("div", "result-payment");
    payment.append(element("strong", "", `+${event.score_deltas.find((d) => d.player_id === event.winner_id)?.delta || 0}`), "分"); summary.append(payment);
    const groups = element("div", "result-groups");
    for (const values of win.groups) { const group = element("div", "result-group"); displayFormula(values).forEach((v) => group.append(tile(v))); groups.append(group); }
    content.append(summary, groups);
    for (const item of win.fan_items) {
      const row = element("div", "fan-row"); const name = element("span", "", item.name);
      if (item.count > 1) name.append(element("small", "", `×${item.count}`));
      row.append(name, element("span", "", `${item.total_fan} 番`)); content.append(row);
    }
    const payers = event.score_deltas.filter((item) => item.delta < 0).map((item) => state.players.find((player) => player.player_id === item.player_id)?.name).join("、");
    content.append(element("p", "result-method", `${win.win_method} · ${win.win_type} · 起和 ${win.starting_fan} 番 · 计分 ${win.capped_fan} 番 · 每家 ${win.base_payment} 分${singlePlayer ? ` · 付款：${payers}` : ""}`));
  }
  if (!events.length) content.append(element("p", "draw-result", "牌山已空，本局分数不变。"));
  if (singlePlayer) {
    $("next-round-button").hidden = !finished;
    for (const player of state.players) {
      const delta = result.score_deltas.find((item) => item.player_id === player.player_id)?.delta || 0;
      content.append(element("p", "fan-row", `${player.name} · 座位 ${player.seat + 1} · 本局 ${delta >= 0 ? "+" : ""}${delta} · 累计 ${player.score} 分`));
    }
    $("next-round-button").disabled = false;
  }
  if (!$("result-dialog").open) $("result-dialog").showModal();
}

function displayFormula(values) {
  // Calculator groups are operator-first; this is presentation, not validation.
  const op = values.find((v) => v === "+" || v === "×");
  const numbers = values.filter((v) => typeof v === "number").sort((a, b) => a - b);
  if (!op || values.length !== 4 || numbers.length !== 3) return values;
  for (let left = 0; left < 3; left++) {
    for (let right = left + 1; right < 3; right++) {
      const result = numbers[3 - left - right];
      if ((op === "+" ? numbers[left] + numbers[right] : numbers[left] * numbers[right]) === result) return [numbers[left], op, numbers[right], result];
    }
  }
  return values;
}

$("scene-select").onchange = () => send("scene", { scene: $("scene-select").value });
function renderRecovery() {
  if (singlePlayer) return;
  const dialog = $("recovery-dialog");
  if (!ui().recovery_pending) { if (dialog.open) dialog.close(); return; }
  $("stage-label").textContent = "对局已暂停";
  $("recovery-summary").textContent = `第 ${ui().round_number} 局 · 牌山 ${state.wall.remaining_count} 张`;
  const remaining = ui().recovery_remaining_seconds;
  $("recovery-time").textContent = remaining == null ? "对局已暂停" : `操作剩余 ${Math.ceil(remaining)} 秒`;
  $("recovery-error").textContent = ui().storage_error ? "存档写入失败，对局已退回最后保存点并暂停。" : "";
  for (const id of ["setup-dialog", "editor-dialog", "result-dialog"]) if ($(id).open) $(id).close();
  if (!dialog.open) dialog.showModal();
}
$("recovery-dialog").addEventListener("cancel", (event) => event.preventDefault());
$("recovery-resume").onclick = () => send("resume");
$("recovery-reset").onclick = () => { if (confirm("放弃保存的对局并返回开局设置？")) send("reset", { round_id: state.round_id }); };
$("restart-button").onclick = openSettings;
$("timers-enabled").onchange = () => {
  const toggles = [...document.querySelectorAll('.timer-setting [type="checkbox"]')];
  if ($("timers-enabled").checked && !toggles.some((input) => input.checked)) toggles.forEach((input) => { input.checked = true; });
  syncSettingControls();
};
document.querySelectorAll('[name="game-mode"]').forEach((input) => { input.onchange = syncSettingControls; });
$("setup-form").onsubmit = async (event) => {
  event.preventDefault();
  const timeouts = {};
  document.querySelectorAll(".timer-setting").forEach((row) => {
    timeouts[row.dataset.field] = $("timers-enabled").checked && row.querySelector("[type=checkbox]").checked ? Number(row.querySelector("[type=number]").value) : null;
  });
  if (await send("start", { mode: document.querySelector('[name="game-mode"]:checked').value, scene: $("setup-scene").value, timeouts })) $("setup-dialog").close();
};
$("reset-game").onclick = () => send("reset", { round_id: state.round_id });
$("developer-toggle").onclick = () => $("opponents-panel").classList.toggle("open");
$("opponents-close").onclick = () => $("opponents-panel").classList.remove("open");
$("developer-close").onclick = () => $("developer-panel").classList.remove("open");
$("face-mode").onclick = () => setAppearance(false);
$("number-mode").onclick = () => setAppearance(true);
function setAppearance(value) { numberView = value; $("face-mode").classList.toggle("active", !value); $("number-mode").classList.toggle("active", value); render(); }
$("edit-hand").onclick = () => openEditor("edit_hand");
$("edit-wall").onclick = $("deck-add").onclick = () => openEditor("edit_wall");
$("deck-to-front").onclick = () => { const tiles = [...ui().wall_tiles]; tiles.unshift(...tiles.splice(deckSelection, 1)); deckSelection = 0; send("edit_wall", { tiles }); };
$("deck-delete").onclick = () => { const tiles = [...ui().wall_tiles]; tiles.splice(deckSelection, 1); deckSelection = null; send("edit_wall", { tiles }); };
$("show-result").onclick = showResult;
$("next-round-button").onclick = async () => {
  if (singlePlayer) { await singlePlayer.nextRound(); return; }
  if (await send("next_round", { round_id: state.round_id })) $("result-dialog").close();
};
document.querySelectorAll("[data-close]").forEach((node) => { node.onclick = () => $(node.dataset.close).close(); });
$("editor-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const tiles = JSON.parse($("tile-input").value);
    if (!Array.isArray(tiles)) throw new Error("请输入 JSON 数组");
    if (await send(editorKind, { tiles })) $("editor-dialog").close();
  } catch (error) { $("editor-error").textContent = error.message; }
};
for (const value of VALUES) {
  const button = element("button", "", value); button.type = "button";
  button.title = `添加 ${value}`;
  button.onclick = () => {
    try {
      const tiles = JSON.parse($("tile-input").value);
      if (!Array.isArray(tiles)) throw new Error("请输入 JSON 数组");
      tiles.push(value); $("tile-input").value = JSON.stringify(tiles); $("editor-error").textContent = "";
    } catch (error) { $("editor-error").textContent = error.message; }
  };
  $("tile-palette").append(button);
}
if (singlePlayer) singlePlayer.initialize();
poll();
setInterval(poll, 700);
setInterval(() => { if (state) renderTimer(); }, 100);
