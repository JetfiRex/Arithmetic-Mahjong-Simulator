"use strict";

// This transport is intentionally local-only. The existing board renderer stays shared.
window.localMultiplayer = {
  token: sessionStorage.getItem("mahjong-local-token"),
  entryMode: "create",
  initialized: false,
  lastNotice: "",
  settingsRevision: null,
  reviewKey: null,
  reviewRequest: 0,
  settlementKey: null,
  recoveryShownFor: null,
  socket: null,
  socketBoot: null,
  socketToken: null,
  connectionId: null,
  retryAt: 0,
  connectionShownFor: null,

  ensureSocket() {
    if (!state?.network?.enabled || !this.token) return;
    if (this.socket && (this.socketBoot !== state.server_instance_id || this.socketToken !== this.token)) {
      this.socket.close(); this.socket = null; this.connectionId = null;
    }
    if (this.socket && this.socket.readyState < WebSocket.CLOSING || performance.now() < this.retryAt) return;
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/local/presence`);
    this.socket = socket; this.socketBoot = state.server_instance_id; this.socketToken = this.token;
    socket.onopen = () => socket.send(JSON.stringify({ token: this.token, server_instance_id: this.socketBoot }));
    socket.onmessage = (event) => {
      if (this.socket !== socket || event.data === "pong") return;
      const message = JSON.parse(event.data);
      if (message.ready && message.server_instance_id === state.server_instance_id) {
        this.connectionId = message.connection_id;
        render(); poll();
      }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.connectionId = null; this.socket = null; this.retryAt = performance.now() + 1000;
      if (state) render();
    };
    socket.onerror = () => socket.close();
  },

  async poll() {
    if (this.token && state?.network?.enabled) this.ensureSocket();
    if (!this.token) {
      const response = await fetch("/api/local/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      this.token = data.token;
      sessionStorage.setItem("mahjong-local-token", this.token);
    }
    const response = await fetch("/api/local/state", { headers: { Authorization: `Bearer ${this.token}` }, cache: "no-store" });
    if (response.status === 401) {
      this.token = null; sessionStorage.removeItem("mahjong-local-token");
      this.connectionId = null;
      if (this.socket) { this.socket.close(); this.socket = null; }
      if (state?.network?.enabled) toast("服务器已重新开服或身份已失效，请重新创建或加入房间");
    }
    return response;
  },

  send(type, data) {
    return fetch("/api/local/action", {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.token}` },
      body: JSON.stringify({ type, command_id: crypto.randomUUID(), room_id: state.room?.room_id,
        server_instance_id: state.server_instance_id,
        connection_id: this.connectionId, control_epoch: state.control_epoch,
        room_revision: state.room?.revision, round_id: state.round_id, hand_revision: state.hand_revision,
        turn_window_id: state.turn_window_id, ...data }),
    });
  },

  initialize() {
    this.initialized = true;
    $("local-rule-mode").onchange = () => {
      $("local-blood-bot-note").hidden = !state.bot_play || $("local-rule-mode").value !== "blood_battle";
    };
    $("bot-add").onclick = () => send("add_bot", { profile: !state.bot_play?.profile_states || state.bot_play.profile_states["rl-fast"]?.available ? "rl-fast" : "fast" });
    $("room-resume").onclick = async () => {
      if (!await send("resume_room", { recovery_id: state.room?.recovery?.recovery_id })) $("room-recovery-error").textContent = $("toast").textContent;
    };
    const stack = document.querySelector(".deck-stack");
    const wallButton = element("button", "local-wall-button"); wallButton.id = "local-wall-button";
    wallButton.type = "button"; wallButton.title = "查看本局牌山"; wallButton.setAttribute("aria-label", wallButton.title);
    stack.replaceWith(wallButton); wallButton.append(stack); wallButton.onclick = () => this.openWallReview();
    document.title = "算术麻将 · 本地多人";
    const tools = document.querySelector(".header-tools");
    const settlements = element("button", "tool-button", "胡牌记录"); settlements.id = "local-settlements";
    settlements.hidden = true; settlements.onclick = showResult; tools.prepend(settlements);
    const resultWall = element("button", "secondary-button", "查看牌山");
    resultWall.id = "result-wall-review"; resultWall.type = "button"; resultWall.hidden = true;
    resultWall.onclick = () => { $("result-dialog").close(); this.openWallReview(); };
    $("next-round-button").before(resultWall);
    const newWindow = element("button", "tool-button", "新测试窗口");
    newWindow.id = "local-new-window"; newWindow.prepend(icon("plus")); newWindow.title = "打开独立身份的本地测试窗口";
    newWindow.onclick = () => window.open("/", "_blank", "noopener"); tools.prepend(newWindow);
    $("developer-toggle").title = "房间成员"; $("developer-toggle").setAttribute("aria-label", "打开房间成员");
    $("developer-panel").setAttribute("aria-label", "房间成员");
    const panel = element("section", "room-panel"); panel.id = "room-panel";
    const heading = element("div", "dialog-heading");
    heading.append(element("h2", "", "房间成员"));
    const close = element("button", "icon-button mobile-only"); close.title = "关闭成员面板"; close.setAttribute("aria-label", close.title); close.append(icon("x"));
    close.onclick = () => $("developer-panel").classList.remove("open"); heading.append(close);
    panel.append(heading, element("p", "room-name"), element("div", "room-roster"), element("div", "room-controls"));
    $("developer-panel").append(panel);
    for (const mode of ["create", "join"]) $(`${mode}-tab`).onclick = () => {
      this.entryMode = mode; $("entry-error").textContent = "";
      $("create-tab").classList.toggle("active", mode === "create"); $("join-tab").classList.toggle("active", mode === "join");
      $("entry-submit").textContent = mode === "create" ? "创建房间" : "加入房间";
    };
    $("entry-form").onsubmit = async (event) => {
      event.preventDefault(); $("entry-error").textContent = "";
      await send(this.entryMode, { username: $("local-username").value, room_name: $("local-room-name").value, password: $("local-password").value });
      if (!state.room) $("entry-error").textContent = $("toast").textContent;
      else $("local-password").value = "";
    };
    $("local-timers-enabled").onchange = () => this.syncTimers();
    $("local-save-seats").onclick = async () => {
      if (await send("assign", { seats: this.seats(), room_revision: this.settingsRevision })) $("local-settings").close();
      else $("local-settings-error").textContent = $("toast").textContent;
    };
    $("local-settings-form").onsubmit = async (event) => {
      event.preventDefault();
      const timeouts = {};
      document.querySelectorAll(".local-timer-setting").forEach((row) => {
        timeouts[row.dataset.field] = $("local-timers-enabled").checked && row.querySelector("[type=checkbox]").checked ? Number(row.querySelector("[type=number]").value) : null;
      });
      if (await send("start", { seats: this.seats(), timeouts, mode: $("local-rule-mode").value, allow_multiple_hu: $("local-multiple-hu").checked, room_revision: this.settingsRevision })) $("local-settings").close();
      else $("local-settings-error").textContent = $("toast").textContent;
    };
  },

  seats() { return [...document.querySelectorAll(".local-seat-select")].map((node) => node.value || null); },

  async nextRound() {
    if (!state.room?.is_host) return;
    if (state.room.phase === "REVIEWING" && !await send("prepare_next_round")) return;
    if (state.room?.phase === "ARRANGING") {
      $("result-dialog").close();
      this.openSettings();
    }
  },

  async openWallReview() {
    if (!["REVIEWING", "ARRANGING"].includes(state.room?.phase) || ui().recovery_pending) return;
    const key = `${state.room.room_id}:${state.round_id}:${state.room.phase}`;
    const request = ++this.reviewRequest;
    const dialog = $("wall-review-dialog");
    $("wall-review-content").replaceChildren(); $("wall-review-error").textContent = "";
    $("wall-review-summary").textContent = "读取中";
    if (!dialog.open) dialog.showModal();
    try {
      const query = new URLSearchParams({ room_id: state.room.room_id, round_id: state.round_id });
      const response = await fetch(`/api/local/wall-record?${query}`, { headers: { Authorization: `Bearer ${this.token}` }, cache: "no-store" });
      const payload = await response.json();
      if (request !== this.reviewRequest || this.reviewKey !== key || !dialog.open) return;
      if (!response.ok) throw new Error(payload.error || "牌山读取失败");
      const record = payload.wall_record;
      $("wall-review-summary").textContent = `第 ${record.round_number} 局 · 共 ${record.total_count} 张 · 已取 ${record.drawn_count} 张 · 未摸 ${record.total_count - record.drawn_count} 张`;
      const names = new Map(record.players.map((player) => [player.player_id, player.name]));
      const labels = { deal: "发牌", draw: "摸牌", replacement: "补牌", remaining: "未摸" };
      for (const [kind, label] of [["deal", "起手发牌"], ["play", "行牌取牌"], ["remaining", "未摸部分"]]) {
        const entries = record.tiles.filter((entry) => kind === "play" ? ["draw", "replacement"].includes(entry.kind) : entry.kind === kind);
        const section = element("section", "wall-review-section");
        section.append(element("h3", "", `${label} · ${entries.length} 张`));
        const grid = element("div", "wall-review-grid");
        for (const entry of entries) {
          const cell = element("div", `wall-review-cell ${entry.kind}`); cell.dataset.position = entry.position;
          cell.append(element("span", "review-position", String(entry.position).padStart(3, "0")), tile(entry.tile));
          const caption = names.get(entry.player_id) || labels[entry.kind];
          cell.append(element("span", "review-caption", caption));
          if (entry.kind !== "remaining") cell.append(element("small", "review-kind", labels[entry.kind]));
          cell.title = `${entry.position} · ${caption} · ${labels[entry.kind]}${entry.action_type ? ` · ${LABEL[entry.action_type]}` : ""}`;
          grid.append(cell);
        }
        section.append(grid); $("wall-review-content").append(section);
      }
    } catch (error) {
      if (request !== this.reviewRequest || !dialog.open) return;
      $("wall-review-summary").textContent = "";
      $("wall-review-error").textContent = error.message;
    }
  },

  renderActions() {
    if (stage() === "WON") {
      $("action-status").textContent = `已胡牌 · 第 ${me().win_order} 位 · 等待其余玩家`;
      action("查看胡牌记录", showResult, "primary");
      return true;
    }
    if (state.network?.enabled && !this.connectionId) {
      $("action-status").textContent = "正在恢复连接";
      return true;
    }
    if (ui().connection_paused) {
      $("action-status").textContent = "全桌暂停 · 等待重连";
      action("等待重连", () => $("room-connection-dialog").showModal(), "primary");
      return true;
    }
    if (ui().autopilot) {
      $("action-status").textContent = "本局由系统托管";
      return true;
    }
    if (ui().recovery_pending) {
      $("action-status").textContent = "对局已暂停";
      action("恢复房间", () => $("room-recovery-dialog").showModal(), "primary");
      return true;
    }
    if (!["REVIEWING", "ARRANGING"].includes(state.room?.phase)) return false;
    const review = state.room.phase === "REVIEWING";
    $("action-status").textContent = review ? "赛后查看" : "等待房主安排下一局";
    action("查看结算", showResult);
    action("查看牌山", () => this.openWallReview());
    if (state.room.is_host) action(review ? "下一局" : "安排下一局", () => review ? this.nextRound() : this.openSettings(), "primary", false, "arrow-right");
    return true;
  },

  openSettings() {
    if (!state.room?.is_host) { toast("只有房主可以安排座位和开局"); return; }
    if (ui().connection_paused) { if (!$("room-connection-dialog").open) $("room-connection-dialog").showModal(); return; }
    if (ui().recovery_pending) { if (!$("room-recovery-dialog").open) $("room-recovery-dialog").showModal(); return; }
    if (state.room.phase === "REVIEWING") { toast("请先点击下一局，结束赛后查看"); return; }
    const active = state.room.phase === "PLAYING";
    this.settingsRevision = state.room.revision;
    $("local-settings-error").textContent = "";
    $("local-seats").replaceChildren();
    for (let seat = 0; seat < 4; seat++) {
      const label = element("label", "", `座位 ${seat + 1} · ${state.players[seat].score} 分`);
      const select = element("select", "local-seat-select"); select.setAttribute("aria-label", `座位 ${seat + 1}`); select.disabled = active;
      const empty = element("option", "", "未安排"); empty.value = ""; select.append(empty);
      for (const member of state.room.members) {
        const option = element("option", "", member.name + (member.member_id === state.room.host_id ? "（房主）" : ""));
        option.value = member.member_id; select.append(option);
      }
      select.value = state.room.seats[seat] || ""; label.append(select); $("local-seats").append(label);
    }
    $("local-timer-settings").replaceChildren();
    for (const [field, phase, label, fallback] of TIMER_FIELDS) {
      const value = ui().timeout_settings[field];
      const row = element("div", "timer-setting local-timer-setting"); row.dataset.field = field;
      const enabled = element("input"); enabled.type = "checkbox"; enabled.checked = value !== null; enabled.setAttribute("aria-label", `启用 ${phase}`); enabled.onchange = () => this.syncTimers();
      const seconds = element("input"); seconds.type = "number"; seconds.min = 1; seconds.max = 600; seconds.step = "any"; seconds.value = value ?? fallback; seconds.setAttribute("aria-label", `${phase} 秒数`);
      row.append(enabled, element("strong", "", phase), element("span", "", label), seconds, element("small", "", "秒")); $("local-timer-settings").append(row);
    }
    $("local-timers-enabled").checked = Object.values(ui().timeout_settings).some((value) => value !== null);
    $("local-timers-enabled").disabled = ui().settings_locked;
    $("local-multiple-hu").checked = state.room.allow_multiple_hu; $("local-multiple-hu").disabled = ui().settings_locked;
    $("local-rule-mode").value = state.room.mode || "normal"; $("local-rule-mode").disabled = ui().settings_locked;
    $("local-rule-mode").onchange();
    $("local-save-seats").disabled = active;
    $("local-start").disabled = active || state.room.members.length < 4;
    $("local-start").firstChild.textContent = ui().started ? "确认开局 " : "开始本局 ";
    $("local-settings-title").textContent = state.room.phase === "ARRANGING" ? "安排下一局" : "房主设置";
    $("local-settings-status").textContent = active ? "本局座位和配置已锁定；结束后可在下一局修改" : `${state.room.members.length} / 10 人 · 座位、模式、一炮多响和计时将在确认开局时生效`;
    this.syncTimers();
    this.renderBotSettings();
    if (!$("local-settings").open) $("local-settings").showModal();
  },

  renderBotSettings() {
    const config = state.bot_play;
    $("bot-settings").hidden = !config;
    if (!config || !state.room) return;
    $("bot-model-status").textContent = `CPU · ${config.compute_ms} ms${config.worker_ready ? "" : " · 本局保底"}`;
    const modelList = $("bot-model-list"); modelList.replaceChildren();
    for (const [kind, model] of Object.entries(config.models || {})) {
      const label = config.profile_groups[kind];
      const status = model.available ? `${model.iteration == null ? "冻结" : `第 ${model.iteration} 轮`} · ${model.sha256.slice(0, 12)}` : model.error || "未配置模型";
      modelList.append(element("div", model.available ? "" : "unavailable", `${label} · ${status}`));
    }
    const root = $("bot-profile-list"); root.replaceChildren();
    const locked = state.room.phase === "PLAYING" || ui().recovery_pending || !this.connectionId;
    const bots = state.room.members.filter((m) => m.is_bot);
    for (const bot of bots) {
      const label = element("label", "bot-profile-row", bot.name);
      const select = element("select"); select.setAttribute("aria-label", `${bot.name}策略`); select.dataset.memberId = bot.member_id;
      for (const [kind, title] of Object.entries(config.profile_groups || { all: "策略" })) {
        const group = element("optgroup"); group.label = title;
        for (const [value, text] of Object.entries(config.profiles)) {
          const status = config.profile_states?.[value] || { family: "all", available: true, reason: "" };
          if (status.family !== kind) continue;
          const option = element("option", "", text + (status.available ? "" : "（无模型）"));
          option.value = value; option.disabled = !status.available; option.title = status.reason;
          group.append(option);
        }
        select.append(group);
      }
      select.value = bot.bot_profile; select.disabled = locked;
      select.onchange = () => send("set_bot", { member_id: bot.member_id, profile: select.value });
      label.append(select); root.append(label);
    }
    $("bot-add").disabled = locked || bots.length >= 3 || state.room.members.length >= 10;
  },

  syncTimers() {
    document.querySelectorAll(".local-timer-setting").forEach((row) => {
      const toggle = row.querySelector("[type=checkbox]");
      toggle.disabled = ui().settings_locked || !$("local-timers-enabled").checked;
      row.querySelector("[type=number]").disabled = toggle.disabled || !toggle.checked;
    });
  },

  renderRecovery() {
    const connectionDialog = $("room-connection-dialog");
    if (ui().connection_paused) {
      for (const id of ["local-settings", "result-dialog", "wall-review-dialog", "room-recovery-dialog"]) if ($(id).open) $(id).close();
      $("wall-review-content").replaceChildren(); this.reviewRequest++;
      const key = `${state.server_instance_id}:${state.round_id}:${state.room.connection.waiting.map((m) => m.member_id).join(":")}`;
      $("room-connection-title").textContent = state.room.connection.reason === "server_outage" ? "服务连接恢复中" : "等待玩家重连";
      this.renderConnectionCountdown();
      if (this.connectionShownFor !== key) {
        this.connectionShownFor = key;
        if (!connectionDialog.open) connectionDialog.showModal();
      }
      return;
    }
    this.connectionShownFor = null;
    if (connectionDialog.open) connectionDialog.close();
    const dialog = $("room-recovery-dialog");
    const recovery = state.room?.recovery;
    if (!recovery?.pending) { if (dialog.open) dialog.close(); return; }
    for (const id of ["local-settings", "result-dialog", "wall-review-dialog"]) if ($(id).open) $(id).close();
    $("wall-review-content").replaceChildren(); this.reviewRequest++;
    const returned = new Set(recovery.returned_members);
    $("room-recovery-summary").textContent = `${state.room.name} · 已返回 ${returned.size} / ${recovery.required_members.length}`;
    const root = $("room-recovery-members"); root.replaceChildren();
    for (const memberId of recovery.required_members) {
      const member = state.room.members.find((item) => item.member_id === memberId);
      const row = element("div", "recovery-member");
      row.append(element("span", "", `${member?.name || "原成员"}${memberId === state.room.host_id ? " · 房主" : ""}`), element("span", returned.has(memberId) ? "returned" : "", returned.has(memberId) ? "已返回" : "待返回")); root.append(row);
    }
    const remaining = ui().recovery_remaining_seconds;
    $("room-recovery-time").textContent = remaining == null ? "对局已暂停" : `本人操作剩余 ${Math.ceil(remaining)} 秒 · 已暂停`;
    $("room-recovery-error").textContent = recovery.storage_error ? "存档写入失败，已退回最后保存点。" : "";
    $("room-resume").hidden = !state.room.is_host;
    $("room-resume").disabled = !recovery.can_resume;
    const key = `${state.server_instance_id}:${recovery.recovery_id}`;
    if (this.recoveryShownFor !== key) {
      this.recoveryShownFor = key;
      if (!dialog.open) dialog.showModal();
    }
  },

  renderConnectionCountdown() {
    if (!ui().connection_paused) return;
    const root = $("room-connection-members"); root.replaceChildren();
    const now = state.server_now + (performance.now() - receivedAt) / 1000;
    for (const waiting of state.room.connection.waiting) {
      const member = state.room.members.find((m) => m.member_id === waiting.member_id);
      const row = element("div", "recovery-member");
      row.append(element("span", "", member?.name || "原玩家"), element("span", "", waiting.deadline_at == null ? "等待返回" : `${Math.max(0, Math.ceil(waiting.deadline_at - now))} 秒`));
      root.append(row);
    }
    const remaining = ui().recovery_remaining_seconds;
    $("room-connection-time").textContent = remaining == null ? "牌局已暂停" : `本人操作剩余 ${Math.ceil(remaining)} 秒 · 已暂停`;
  },

  render() {
    if (!this.initialized) this.initialize();
    this.ensureSocket();
    const room = state.room;
    const key = room ? `${room.room_id}:${state.round_id}:${room.phase}` : null;
    if (this.reviewKey !== key) {
      this.reviewKey = key; this.reviewRequest++;
      if ($("wall-review-dialog").open) $("wall-review-dialog").close();
      $("wall-review-content").replaceChildren();
      if (room?.phase === "ARRANGING" && $("result-dialog").open) $("result-dialog").close();
      if (room?.phase === "REVIEWING" && $("local-settings").open) $("local-settings").close();
    }
    const canReview = ["REVIEWING", "ARRANGING"].includes(room?.phase);
    $("local-wall-button").disabled = !canReview || !!ui().recovery_pending;
    $("local-wall-button").title = canReview && !room.wall_review_available ? "本局无完整牌序记录" : "查看本局牌山";
    $("result-wall-review").hidden = !canReview;
    $("result-wall-review").disabled = !!ui().recovery_pending;
    $("result-wall-review").title = canReview && !room.wall_review_available ? "本局无完整牌序记录" : "查看本局完整牌序";
    $("local-wall-button").setAttribute("aria-label", $("local-wall-button").title);
    $("local-entry").hidden = !!room;
    document.querySelector(".workspace").hidden = !room;
    $("mode-label").textContent = (state.bot_play ? "人机试玩" : state.network?.enabled ? "联机试玩" : "本地多人")
      + (state.config?.mode === "blood_battle" ? " · 血战" : "");
    $("entry-title").textContent = state.bot_play ? "人机房间" : state.network?.enabled ? "联机房间" : "本地房间";
    $("entry-submit").disabled = !!state.network?.enabled && !this.connectionId;
    $("restart-button").disabled = !room?.is_host || room.phase === "REVIEWING";
    $("restart-button").title = "房主设置";
    $("table-mode").replaceChildren(document.createTextNode("LOCAL TABLE"), element("span", "", room?.name || ""));
    document.querySelector(".hand-title h1").textContent = viewerId() ? "我的手牌" : `${me().name} · 观战`;
    $("discard-label").textContent = viewerId() ? "我的弃牌" : `${me().name}的弃牌`;
    $("discard-river").setAttribute("aria-label", $("discard-label").textContent);
    if (stage() === "OBSERVING" || stage() === "WAITING_OTHER") $("stage-label").textContent = `${state.players[state.current_turn_seat].name} · ${visiblePhase() || "等待"}`;
    if (stage() === "WON") $("stage-label").textContent = `已胡牌 · 第 ${me().win_order} 位`;
    const settlements = $("local-settlements"), count = state.settlement_events?.length || 0;
    settlements.hidden = state.config?.mode !== "blood_battle" || !count;
    settlements.textContent = `胡牌记录 (${count}/3)`;
    const settlementKey = `${state.round_id}:${count}:${state.round_result?.end_reason || ""}`;
    if (this.settlementKey !== settlementKey) {
      this.settlementKey = settlementKey;
      if (!settlements.hidden && $("result-dialog").open) showResult();
    }
    if (room?.phase === "REVIEWING") $("stage-label").textContent = "赛后查看";
    if (room?.phase === "ARRANGING") $("stage-label").textContent = "安排下一局";
    if (ui().recovery_pending) $("stage-label").textContent = ui().connection_paused ? "等待重连 · 全桌暂停" : "对局已暂停";
    if (ui().autopilot) $("stage-label").textContent = "本局由系统托管";
    if (state.notice && state.notice !== this.lastNotice) { this.lastNotice = state.notice; toast(state.notice); }
    if (!room) { if ($("local-settings").open) $("local-settings").close(); if ($("result-dialog").open) $("result-dialog").close(); return; }
    if ($("local-settings").open) this.renderBotSettings();
    const panel = $("room-panel"); panel.querySelector(".room-name").textContent = `${room.name} · ${room.members.length} / 10${room.persistent ? " · 自动存档" : ""}`;
    const roster = panel.querySelector(".room-roster"); roster.replaceChildren();
    const active = room.phase === "PLAYING";
    if ($("local-settings").open && this.settingsRevision !== room.revision) {
      // Preserve unsaved timer/seat choices when another test window joins.
      document.querySelectorAll(".local-seat-select").forEach((select) => {
        const selected = select.value;
        select.replaceChildren();
        const empty = element("option", "", "未安排"); empty.value = ""; select.append(empty);
        for (const member of room.members) {
          const option = element("option", "", member.name); option.value = member.member_id; select.append(option);
        }
        select.value = room.members.some((member) => member.member_id === selected) ? selected : "";
        select.disabled = active;
      });
      this.settingsRevision = room.revision;
      $("local-start").disabled = active || room.members.length < 4;
      $("local-save-seats").disabled = active;
    }
    for (const member of room.members) {
      const row = element("div", "room-member"); row.dataset.memberId = member.member_id;
      const seat = (active ? room.round_members : room.seats).indexOf(member.member_id);
      const name = element("div", "member-name", `${member.name}${member.member_id === state.session_id ? "（我）" : ""}`);
      name.append(element("small", "", [member.member_id === room.host_id ? "房主" : "", seat >= 0 ? `座位 ${seat + 1}` : member.spectator_mode === "god" ? "上帝观战" : "公共观战", member.is_bot ? "Bot" : member.autopilot ? "系统托管" : ({ online: "在线", reconnecting: "等待重连", offline: "连接中断" })[member.connection_status]].filter(Boolean).join(" · ")));
      row.append(name);
      if (room.is_host && member.member_id !== room.host_id) {
        const kick = element("button", "icon-button"); kick.title = `移出 ${member.name}`; kick.setAttribute("aria-label", kick.title); kick.append(icon("x")); kick.disabled = active && seat >= 0;
        kick.disabled ||= !!ui().recovery_pending;
        kick.onclick = () => { if (confirm(`将 ${member.name} 移出房间？`)) send("kick", { member_id: member.member_id }); }; row.append(kick);
      }
      roster.append(row);
    }
    const controls = panel.querySelector(".room-controls"); controls.replaceChildren();
    if (!viewerId()) {
      const modes = element("div", "segmented"); modes.setAttribute("aria-label", "观战视角");
      for (const [value, label] of [["public", "公共视角"], ["god", "上帝视角"]]) {
        const button = element("button", room.spectator_mode === value ? "active" : "", label);
        button.disabled = !!ui().recovery_pending;
        button.onclick = () => send("spectate", { mode: value }); modes.append(button);
      }
      controls.append(modes);
    }
    if (room.is_host) {
      const settings = element("button", "secondary-button", ui().recovery_pending ? "恢复房间" : room.phase === "REVIEWING" ? "下一局" : room.phase === "ARRANGING" ? "安排下一局" : "房主设置");
      settings.onclick = () => ui().recovery_pending ? this.openSettings() : room.phase === "REVIEWING" ? this.nextRound() : this.openSettings(); controls.append(settings);
    }
    const leave = element("button", "text-button", room.is_host ? "关闭房间" : "离开房间");
    leave.disabled = !room.is_host && ((active && !!viewerId()) || !!ui().recovery_pending);
    leave.onclick = () => { if (confirm(room.is_host ? "关闭房间并让所有成员离开？" : "离开这个房间？")) send("leave"); }; controls.append(leave);
    $("next-round-button").disabled = !room.is_host;
  },
};
