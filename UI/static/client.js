"use strict";

window.singlePlayer = {
  initialized: false,
  settingsRevision: null,
  setupInstance: null,
  reviewKey: null,
  reviewRequest: 0,
  settlementKey: null,
  currentMode: "home",
  normalSetupPending: false,
  modeTitles: { home: "首页", tutorial: "新手教程", normal: "普通模式", teaching: "教学模式", calculator: "计算模式", practice: "听牌练习", replay: "复盘回放" },

  isTable() { return ["normal", "teaching"].includes(this.currentMode); },
  poll() { return fetch(this.currentMode === "teaching" ? "/api/state?teaching=true" : "/api/state", { cache: "no-store" }); },

  send(type, data) {
    return fetch("/api/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, command_id: crypto.randomUUID(), instance_id: state.instance_id,
        settings_revision: state.table.revision, round_id: state.round_id, hand_revision: state.hand_revision,
        turn_window_id: state.turn_window_id, view: this.currentMode === "teaching" ? "teaching" : "normal", ...data }),
    });
  },

  initialize() {
    this.initialized = true;
    window.listeningPractice.initialize();
    window.handCalculator.initialize();
    window.tableStudy.initialize();
    window.beginnerTutorial.initialize();
    window.ruleReference.initialize();
    $("local-rule-mode").onchange = () => {
      $("local-blood-bot-note").hidden = $("local-rule-mode").value !== "blood_battle";
    };
    const stack = document.querySelector(".deck-stack");
    const wallButton = element("button", "local-wall-button"); wallButton.id = "local-wall-button";
    wallButton.type = "button"; wallButton.title = "查看本局牌山"; wallButton.setAttribute("aria-label", wallButton.title);
    stack.replaceWith(wallButton); wallButton.append(stack); wallButton.onclick = () => this.openWallReview();
    const settlements = element("button", "tool-button", "胡牌记录"); settlements.id = "local-settlements";
    settlements.hidden = true; settlements.onclick = showResult; document.querySelector(".header-tools").prepend(settlements);
    const resultWall = element("button", "secondary-button", "查看牌山");
    resultWall.id = "result-wall-review"; resultWall.type = "button"; resultWall.hidden = true;
    resultWall.onclick = () => { $("result-dialog").close(); this.openWallReview(); };
    $("next-round-button").before(resultWall);
    const replayButton = element("button", "secondary-button", "复盘本局"); replayButton.id = "result-replay";
    replayButton.onclick = () => { $("result-dialog").close(); window.tableStudy.preferredReplay = state.round_id; location.hash = "replay"; };
    $("next-round-button").before(replayButton);
    $("local-timers-enabled").onchange = () => this.syncTimers();
    $("local-settings-form").onsubmit = async (event) => {
      event.preventDefault();
      const timeouts = {};
      document.querySelectorAll(".local-timer-setting").forEach((row) => {
        timeouts[row.dataset.field] = $("local-timers-enabled").checked && row.querySelector("[type=checkbox]").checked ? Number(row.querySelector("[type=number]").value) : null;
      });
      if (await send("start", { timeouts, hand_preparation_seconds: Number($("local-hand-preparation").value),
        mode: $("local-rule-mode").value, allow_multiple_hu: $("local-multiple-hu").checked,
        settings_revision: this.settingsRevision, experience: this.currentMode === "teaching" ? "teaching" : "normal" })) $("local-settings").close();
      else $("local-settings-error").textContent = $("toast").textContent;
    };
    this.initializeNavigation();
  },

  initializeNavigation() {
    document.querySelectorAll("[data-mode]").forEach((button) => {
      button.onclick = () => { location.hash = button.dataset.mode; };
    });
    document.querySelectorAll("[data-helper-tabs]").forEach((root) => {
      const tabs = [...root.querySelectorAll("[role=tab]")];
      const select = (tab) => {
        for (const item of tabs) {
          item.setAttribute("aria-selected", String(item === tab));
          item.tabIndex = item === tab ? 0 : -1;
        }
        root.querySelectorAll("[data-helper-panel]").forEach((panel) => {
          panel.hidden = panel.dataset.helperPanel !== tab.dataset.helper;
        });
      };
      tabs.forEach((tab, index) => {
        tab.onclick = () => select(tab);
        tab.onkeydown = (event) => {
          const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
            Home: 0, End: tabs.length - 1 }[event.key];
          if (next === undefined) return;
          event.preventDefault(); select(tabs[next]); tabs[next].focus();
        };
      });
    });
    window.addEventListener("hashchange", () => this.navigate(location.hash.slice(1)));
    this.navigate(location.hash.slice(1));
  },

  preparePreview(mode) {
    const root = document.querySelector(`[data-mode-screen="${mode}"]`);
    if (!root || root.dataset.prepared) return;
    root.dataset.prepared = "true";
    root.querySelectorAll("[data-hand-preview]").forEach((row) => {
      for (let index = 0; index < 13; index++) {
        const slot = element("span", row.dataset.handPreview === "backs" ? "preview-card-back" : "preview-card-slot");
        slot.setAttribute("aria-hidden", "true");
        if (row.dataset.handPreview === "slots") slot.textContent = index + 1;
        row.append(slot);
      }
    });
    root.querySelectorAll("[data-palette-preview]").forEach((palette) => {
      for (const value of VALUES) {
        const node = tile(value, true);
        node.disabled = true;
        palette.append(node);
      }
    });
  },

  navigate(mode) {
    const next = Object.hasOwn(this.modeTitles, mode) ? mode : "home";
    const previous = this.currentMode;
    if (this.currentMode !== next) {
      document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
      if (dragging) endDrag(false);
      document.querySelectorAll(".developer-panel.open").forEach((panel) => panel.classList.remove("open"));
      this.normalSetupPending = ["normal", "teaching"].includes(next);
      this.currentMode = next;
      window.scrollTo(0, 0);
    }
    this.preparePreview(next);
    this.renderNavigation();
    if (next === "practice") window.listeningPractice.enter();
    else window.listeningPractice.leave();
    if (next === "calculator") window.handCalculator.enter();
    else window.handCalculator.clearDrag();
    if (next === "tutorial" && previous !== next) window.beginnerTutorial.enter();
    else if (next !== "tutorial") window.beginnerTutorial.leave();
    if (previous !== next) window.tableStudy.enter(next, previous);
    if (state && this.isTable()) { render(); poll(); }
  },

  renderNavigation() {
    const mode = this.currentMode;
    document.body.dataset.screen = mode;
    $("mode-home").hidden = mode !== "home";
    $("app-header").hidden = mode === "home";
    $("normal-workspace").hidden = !this.isTable();
    $("mode-preview").hidden = ["home", "normal", "teaching"].includes(mode);
    document.querySelectorAll("[data-mode-screen]").forEach((screen) => {
      screen.hidden = screen.dataset.modeScreen !== mode;
    });
    $("mode-label").textContent = this.modeTitles[mode];
    document.title = mode === "home" ? "算术麻将 · 单机版" : `${this.modeTitles[mode]} · 算术麻将`;
    const phase = state?.table?.phase;
    const notice = $("home-game-notice");
    notice.hidden = !["PLAYING", "REVIEWING", "ARRANGING"].includes(phase);
    const message = phase === "PLAYING" ? state?.table.paused ? "当前牌局已暂停。返回牌桌后点击继续对局。" : "当前牌局仍在进行，计时继续。返回普通模式或教学模式即可继续对局。"
      : phase === "REVIEWING" ? "本局已结束，返回普通模式可查看结算。" : "已准备安排下一局，返回普通模式可继续设置。";
    if (notice.textContent !== message) notice.textContent = message;
    if (this.isTable() && state && this.normalSetupPending) {
      this.normalSetupPending = false;
      if (["SETUP", "ARRANGING"].includes(phase)) this.openSettings();
    }
  },

  async nextRound() {
    if (state.table.phase === "REVIEWING" && !await send("prepare_next_round")) return;
    if (state.table.phase === "ARRANGING") {
      $("result-dialog").close();
      this.openSettings();
    }
  },

  renderActions() {
    if (state.table.paused) {
      $("action-status").textContent = "对局已暂停，可以整理手牌或查看分析";
      action("继续对局", () => send("resume"), "primary");
      return true;
    }
    if (stage() === "HAND_PREPARATION") {
      $("action-status").textContent = "开局理牌中，可以拖动整理手牌；倒计时结束后开始行牌";
      return true;
    }
    if (stage() === "WON") {
      $("action-status").textContent = `已胡牌 · 第 ${me().win_order} 位 · 等待其余 Bot`;
      action("查看胡牌记录", showResult, "primary");
      return true;
    }
    if (!["REVIEWING", "ARRANGING"].includes(state.table.phase)) return false;
    const review = state.table.phase === "REVIEWING";
    $("action-status").textContent = review ? "赛后查看" : "安排下一局";
    action("查看结算", showResult);
    action("查看牌山", () => this.openWallReview());
    action(review ? "下一局" : "开局设置", () => review ? this.nextRound() : this.openSettings(), "primary", false, "arrow-right");
    return true;
  },

  openSettings() {
    if (state.table.phase === "REVIEWING") { toast("请先点击下一局，结束赛后查看"); return; }
    this.settingsRevision = state.table.revision;
    $("local-settings-error").textContent = "";
    $("local-timer-settings").replaceChildren();
    for (const [field, phase, label, fallback] of TIMER_FIELDS) {
      const value = ui().timeout_settings[field];
      const row = element("div", "timer-setting local-timer-setting"); row.dataset.field = field;
      const enabled = element("input"); enabled.type = "checkbox"; enabled.checked = value !== null; enabled.setAttribute("aria-label", `启用${label}`); enabled.onchange = () => this.syncTimers();
      const seconds = element("input"); seconds.type = "number"; seconds.min = 1; seconds.max = 600; seconds.step = "any"; seconds.value = value ?? fallback; seconds.setAttribute("aria-label", `${label}秒数`);
      row.append(enabled, element("strong", "", label), seconds, element("small", "", "秒")); $("local-timer-settings").append(row);
    }
    $("local-timers-enabled").checked = this.currentMode === "teaching" && state.table.phase === "SETUP" ? false : Object.values(ui().timeout_settings).some((value) => value !== null);
    $("local-timers-enabled").disabled = ui().settings_locked;
    $("local-hand-preparation").value = state.table.hand_preparation_seconds ?? 30;
    $("local-hand-preparation").disabled = ui().settings_locked;
    $("local-multiple-hu").checked = state.table.allow_multiple_hu; $("local-multiple-hu").disabled = ui().settings_locked;
    $("local-rule-mode").value = state.table.mode; $("local-rule-mode").disabled = ui().settings_locked;
    $("local-rule-mode").onchange();
    $("local-start").disabled = ui().settings_locked;
    $("local-start").firstChild.textContent = ui().started ? "确认开局 " : "开始本局 ";
    $("local-settings-title").textContent = state.table.phase === "ARRANGING" ? "安排下一局" : this.currentMode === "teaching" ? "教学对局设置" : "单机设置";
    $("local-settings-status").textContent = ui().settings_locked ? "本局配置已锁定；结束后可在下一局修改" : "你与三家 Bot 对局；模式、一炮多响和计时将在确认开局时生效";
    this.syncTimers(); this.renderBotSettings();
    if (!$("local-settings").open) $("local-settings").showModal();
  },

  async openWallReview() {
    if (!["REVIEWING", "ARRANGING"].includes(state.table?.phase)) return;
    const key = `${state.instance_id}:${state.round_id}:${state.table.phase}`;
    const request = ++this.reviewRequest;
    const dialog = $("wall-review-dialog");
    $("wall-review-content").replaceChildren(); $("wall-review-error").textContent = "";
    $("wall-review-summary").textContent = "读取中";
    if (!dialog.open) dialog.showModal();
    try {
      const query = new URLSearchParams({ round_id: state.round_id });
      const response = await fetch(`/api/wall-record?${query}`, { cache: "no-store" });
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

  renderBotSettings() {
    const config = state.bot_play;
    $("bot-settings").hidden = !config;
    if (!config || !state.table) return;
    renderSection("bot-settings", [config, state.table.phase], () => {
      $("bot-model-status").textContent = `CPU · ${config.compute_ms} ms${config.worker_ready ? "" : " · 本局保底"}`;
      const modelList = $("bot-model-list"); modelList.replaceChildren();
      for (const [kind, model] of Object.entries(config.models || {})) {
        const label = config.profile_groups[kind];
        const status = model.available ? `${model.iteration == null ? "冻结" : `第 ${model.iteration} 轮`} · ${model.sha256.slice(0, 12)}` : model.error || "未配置模型";
        modelList.append(element("div", model.available ? "" : "unavailable", `${label} · ${status}`));
      }
      const root = $("bot-profile-list"); root.replaceChildren();
      const locked = state.table.phase === "PLAYING";
      const bots = config.opponents;
      for (const bot of bots) {
        const label = element("label", "bot-profile-row", bot.name);
        const select = element("select"); select.setAttribute("aria-label", `${bot.name}策略`); select.dataset.playerId = bot.player_id;
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
        select.value = bot.profile; select.disabled = locked;
        select.onchange = () => send("set_bot", { player_id: bot.player_id, profile: select.value });
        label.append(select); root.append(label);
      }
    });
  },

  syncTimers() {
    document.querySelectorAll(".local-timer-setting").forEach((row) => {
      const toggle = row.querySelector("[type=checkbox]");
      toggle.disabled = ui().settings_locked || !$("local-timers-enabled").checked;
      row.querySelector("[type=number]").disabled = toggle.disabled || !toggle.checked;
    });
  },

  render() {
    if (!this.initialized) this.initialize();
    window.tableStudy.sync();
    if (!this.isTable()) {
      this.setupInstance = state.instance_id;
      this.renderNavigation();
      return;
    }
    const table = state.table;
    const key = `${state.instance_id}:${state.round_id}:${table.phase}`;
    if (this.reviewKey !== key) {
      this.reviewKey = key; this.reviewRequest++;
      if ($("wall-review-dialog").open) $("wall-review-dialog").close();
      $("wall-review-content").replaceChildren();
      if (table.phase === "ARRANGING" && $("result-dialog").open) $("result-dialog").close();
      if (table.phase === "REVIEWING" && $("local-settings").open) $("local-settings").close();
    }
    const canReview = ["REVIEWING", "ARRANGING"].includes(table.phase);
    $("local-wall-button").disabled = !canReview || !table.wall_review_available;
    $("result-wall-review").hidden = !canReview;
    $("result-replay").hidden = !canReview;
    $("result-wall-review").disabled = !table.wall_review_available;
    $("mode-label").textContent = (this.currentMode === "teaching" ? "教学对局" : "单机人机对局") + (state.config?.mode === "blood_battle" ? " · 血战" : "");
    if (stage() === "HAND_PREPARATION") {
      $("clock-label").textContent = "开局理牌";
      $("incoming-caption").textContent = "理牌结束后庄家摸牌";
    }
    $("restart-button").disabled = table.phase === "REVIEWING";
    $("restart-button").title = "单机设置";
    renderSection("table-mode", table.mode, () => {
      $("table-mode").replaceChildren(document.createTextNode("SINGLE PLAYER"), element("span", "", table.mode === "blood_battle" ? "血战模式" : "普通模式"));
    });
    $("discard-label").textContent = "我的弃牌";
    if (stage() === "WAITING_OTHER") $("stage-label").textContent = "等待 Bot 选择吃法";
    if (stage() === "WON") $("stage-label").textContent = `已胡牌 · 第 ${me().win_order} 位`;
    if (table.phase === "REVIEWING") $("stage-label").textContent = "赛后查看";
    if (table.phase === "ARRANGING") $("stage-label").textContent = "安排下一局";
    const settlements = $("local-settlements"), count = state.settlement_events?.length || 0;
    settlements.hidden = state.config?.mode !== "blood_battle" || !count;
    settlements.textContent = `胡牌记录 (${count}/3)`;
    const settlementKey = `${state.round_id}:${count}:${state.round_result?.end_reason || ""}`;
    if (this.settlementKey !== settlementKey) {
      this.settlementKey = settlementKey;
      if (!settlements.hidden && $("result-dialog").open) showResult();
    }
    renderSection("roster", [state.bot_play.opponents, state.players.map((p) => p.seat)], () => {
      const roster = $("opponent-roster"); roster.replaceChildren();
      for (const bot of state.bot_play.opponents) {
        const row = element("div", "table-opponent", bot.name);
        row.append(element("small", "", `座位 ${state.players.find(p => p.player_id === bot.player_id).seat + 1}`));
        roster.append(row);
      }
    });
    if ($("local-settings").open) {
      this.settingsRevision = table.revision;
      this.renderBotSettings();
    }
    if (this.setupInstance !== state.instance_id) {
      this.setupInstance = state.instance_id;
      if (this.isTable()) this.normalSetupPending = true;
    }
    this.renderNavigation();
  },
};
