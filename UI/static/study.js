"use strict";

window.tableStudy = {
  key: null, command: null, result: null, calculating: false, timer: null,
  replay: null, replayId: null, index: 0, playback: null, loading: false, lastTableMode: "normal",

  initialize() {
    $("study-player").onchange = () => this.sync();
    $("study-perspective").onchange = () => this.sync();
    $("study-auto").onchange = () => {
      clearTimeout(this.timer);
      if ($("study-auto").checked) this.schedule();
      else if (this.result && !this.result.complete) {
        $("study-completeness").textContent = this.calculating ? "本轮分析中" : "已暂停";
        $("study-status").textContent = this.calculating ? "本轮结束后暂停续算。" : "已暂停续算，可以点击“继续分析”。";
      }
    };
    try { const saved = localStorage.getItem("arithmetic-study-limit"); if (["0","3","5","10"].includes(saved)) $("study-limit").value = saved; } catch (_) {}
    $("study-limit").onchange = () => {
      try { localStorage.setItem("arithmetic-study-limit", $("study-limit").value); } catch (_) {}
      if (this.result && this.command) this.renderAnalysis(this.result,this.command,$("study-results"));
    };
    $("study-analyze").onclick = () => { this.stop(); this.analyze(false); };
    $("study-pause").onclick = () => send(state.table.paused ? "resume" : "pause");
    $("study-reveal").onchange = () => renderOpponents();
    $("study-to-analysis").onclick = () => $("study-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    $("replay-refresh").onclick = () => this.refresh();
    $("replay-select").onchange = () => this.load($("replay-select").value);
    $("replay-import").onclick = () => $("replay-file").click();
    $("replay-file").onchange = () => this.importFile();
    $("replay-save").onclick = () => this.save();
    $("replay-prev").onclick = () => { this.stop(); this.seek(this.index - 1); };
    $("replay-next").onclick = () => { this.stop(); this.seek(this.index + 1); };
    $("replay-progress").oninput = () => { this.stop(); this.seek(Number($("replay-progress").value)); };
    $("replay-play").onclick = () => this.playback ? this.stop() : this.play();
    $("replay-speed").onchange = () => { if (this.playback) { this.stop(); this.play(); } };
    $("replay-viewer").onchange = () => {
      $("study-player").value = $("replay-viewer").value; this.renderReplay(); this.sync();
    };
    $("replay-reveal").onchange = () => this.renderReplay();
    $("replay-wall-toggle").onchange = () => this.renderReplay();
    $("replay-return").onclick = () => { location.hash = this.lastTableMode; };
  },

  async enter(mode, previous) {
    clearTimeout(this.timer);
    this.stop(false);
    const active = ["teaching", "replay"].includes(mode);
    $("study-panel").hidden = !active;
    $("study-toolbar").hidden = mode !== "teaching";
    if (["normal", "teaching"].includes(previous)) this.lastTableMode = previous;
    const parent = mode === "replay" ? $("replay-analysis") : $("normal-workspace");
    if ($("study-panel").parentElement !== parent) parent.append($("study-panel"));
    if (mode === "replay") {
      if (busy) await new Promise(resolve => window.addEventListener("table-action-idle", resolve, { once: true }));
      if (singlePlayer.currentMode !== "replay") return;
      if (state?.table.phase === "PLAYING" && !state.table.paused && !await send("pause")) {
        this.replayError("当前对局尚未暂停，请返回牌桌确认后再打开回放。"); return;
      }
      $("replay-return").hidden = !state?.ui.started;
      await this.refresh(this.preferredReplay || this.replayId);
      this.preferredReplay = null;
    }
    this.sync();
    if (active && !this.result?.complete && $("study-auto").checked) this.schedule();
  },

  frame() {
    if (singlePlayer.currentMode === "replay") return this.replay?.frames[this.index] || null;
    if (singlePlayer.currentMode !== "teaching" || !state?.ui.started || !state.study) return null;
    return { ...state, drawn_tile_id: state.study.drawn_tile_id, min_fan: state.study.min_fan,
      claimed_tiles: state.study.claimed_tiles, self_draw_allowed: state.study.self_draw_allowed };
  },

  position(frame) {
    const pid = $("study-player").value, all = $("study-perspective").value === "omniscient";
    const player = frame.players.find(p => p.player_id === pid);
    if (!player?.hand_tiles || player.is_winner) return null;
    const size = 14 - player.melded_groups.length * 4;
    if (![size - 1, size].includes(player.hand_tiles.length)) return null;
    const visible = [...(frame.claimed_tiles || []), ...frame.players.flatMap(p => [...p.discarded_tiles,
      ...(p.player_id === pid ? [] : p.melded_groups.flatMap(m => m.tiles)),
      ...(all && p.player_id !== pid ? p.hand_tiles : [])])];
    const order = (a,b) => VALUES.indexOf(a) - VALUES.indexOf(b);
    const drawn = player.hand_tile_records.find(t => t.tile_id === frame.drawn_tile_id);
    const body = { hand: [...player.hand_tiles].sort(order), visible: visible.sort(order),
      melds: player.melded_groups.map(m => ({ group_type: m.group_type, tiles: [...m.tiles] })),
      min_fan: frame.min_fan, draws_left: null,
      context: { self_draw_allowed: !!drawn && frame.self_draw_allowed !== false, is_heavenly: !frame.discard_history.some(r => r.player_id === pid) } };
    if (drawn) body.winning_tile = drawn.value;
    return body;
  },

  sync() {
    if (!["teaching", "replay"].includes(singlePlayer.currentMode)) return;
    const frame = this.frame(), command = frame ? this.position(frame) : null;
    const all = $("study-perspective").value === "omniscient";
    $("study-perspective-note").textContent = all ? "扣除其他三家的暗牌，按剩余牌山的牌种数量计算；不使用未来摸牌顺序。" : "仅使用该玩家的手牌、自己的副露和桌面公开牌；显示明牌开关不改变这一口径。";
    $("study-pause").disabled = state?.table.phase !== "PLAYING";
    $("study-pause").textContent = state?.table.paused ? "继续对局" : "暂停思考";
    const key = JSON.stringify([singlePlayer.currentMode, frame?.round_id, $("study-player").value, all, command]);
    if (key !== this.key) {
      this.key = key; this.command = command; this.result = null;
      clearTimeout(this.timer);
      $("study-results").replaceChildren();
      $("study-completeness").textContent = "等待分析";
      $("study-status").textContent = command ? "局面已更新。" : "开局或选择牌谱后，可分析当前玩家的手牌。";
      if (frame?.players.find(p => p.player_id === $("study-player").value)?.is_winner) {
        $("study-status").textContent = "该玩家已经胡牌，以下展示本局实际结算。";
        $("study-completeness").textContent = "已胡牌";
        for (const event of frame.settlement_events || []) if (event.winner_id === $("study-player").value)
          this.settlement($("study-results"), event, frame);
      }
      if (command && $("study-auto").checked) this.schedule();
    }
    this.controls();
  },
  controls() {
    $("study-analyze").disabled = this.calculating || !this.command;
    $("study-analyze").textContent = this.calculating ? "正在分析…" : this.result && !this.result.complete ? "继续分析" : "分析当前局面";
  },
  schedule(delay = 450) {
    clearTimeout(this.timer);
    if (!this.calculating && !this.playback && this.command && !this.result?.complete && ["teaching", "replay"].includes(singlePlayer.currentMode))
      this.timer = setTimeout(() => this.analyze(!this.result), delay);
  },
  async analyze(quick) {
    if (!this.command || this.calculating) return;
    clearTimeout(this.timer);
    const key = this.key, command = this.command;
    let resume = false, delay = 250;
    this.calculating = true; this.controls();
    $("study-status").textContent = "后台分析中；可以继续操作牌桌。";
    try {
      const result = await handCalculator.request({ ...command, quick });
      if (key !== this.key || !["teaching", "replay"].includes(singlePlayer.currentMode)) return;
      this.result = result;
      this.renderAnalysis(result, command, $("study-results"));
      const automatic = $("study-auto").checked;
      $("study-completeness").textContent = result.complete ? "已完成" : automatic ? "持续分析中" : "已暂停";
      const candidates = result.candidates.filter(row => row.eligible === true), completed = candidates.filter(row => row.complete).length;
      $("study-status").textContent = result.complete ? "当前局面分析完成。" : automatic ? `正在持续分析，第 ${result.passes || 1} 轮${candidates.length ? `，已完成 ${completed}/${candidates.length} 个出牌候选` : ""}；结果会自动更新。` : "本轮进度已保存，点击继续分析可接着计算。";
      resume = !result.complete;
    } catch (error) {
      if (key === this.key) { $("study-status").textContent = error.message; resume = error.status === 429; delay = 1500; }
    }
    finally {
      this.calculating = false; this.controls();
      if ($("study-auto").checked && (key !== this.key || resume)) this.schedule(delay);
    }
  },

  actualDiscard() {
    if (singlePlayer.currentMode !== "replay" || !this.replay) return null;
    for (const frame of this.replay.frames.slice(this.index + 1)) {
      for (const event of frame.events || []) {
        if (event.type === "tile_discarded") return event.player_id === $("study-player").value ? event.tile : null;
        if (event.type === "win_settled" || event.type === "meld_created") return null;
      }
    }
    return null;
  },

  settlement(root, event, frame) {
    const won = event.win_result, player = frame.players.find(p => p.player_id === event.winner_id);
    root.append(element("h3", "calc-hint", `${player.name} · ${won.win_method} · ${won.total_fan} 番`));
    root.append(element("p", "calc-hint", (won.fan_items || []).map(f => `${f.name} ${f.total_fan}番`).join("、")));
    let marked = false;
    const groups = won.groups.map(values => ({ label: "", tiles: values.map(value => {
      const winning = !marked && value === won.winning_tile; if (winning) marked = true; return { value, winning };
    }) }));
    handCalculator.renderGroups(root, groups);
  },

  renderAnalysis(result, command, root) {
    root.replaceChildren();
    const summary = element("div", "study-summary");
    for (const [label, value] of [["向听", handCalculator.shantenText(result.shanten)], ["连续进张价值", this.score(result.value)], ["未见张数", result.unseen_total]]) {
      const cell = element("div"); cell.append(element("span", "", label), element("strong", "", value)); summary.append(cell);
    }
    root.append(summary);
    if (result.win) handCalculator.renderWin(root, result.win);
    if (!result.drawn) { this.improvements(root, result.improvements, command); return; }
    const actual = this.actualDiscard();
    if (actual !== null) {
      const row = result.candidates.find(r => r.discard === actual);
      const note = !row ? "" : row.eligible === false ? "；该方案增加了结构向听。" : result.complete && row.value !== null ? `；与最高连续进张评价相差 ${handCalculator.format(result.value - row.value)}。` : "；评分尚未完成。";
      root.append(element("p", "study-actual-note", `本局实际打 ${actual}${note}`));
    }
    const limit = Number($("study-limit").value);
    const ordered = [...result.candidates].sort((a,b) => (a.shanten ?? 99)-(b.shanten ?? 99) || (b.value ?? -1)-(a.value ?? -1));
    const eligible = ordered.filter(row => row.eligible !== false);
    const shown = limit ? eligible.slice(0,limit) : ordered;
    const identifying = !result.complete && (!ordered.length || ordered.some(row => row.eligible === null));
    root.append(element("p", "calc-hint study-list-note", identifying ? "正在确定最低向听的候选，计算结果会自动更新。" : limit ? `显示 ${eligible.length} 个最低向听候选中的前 ${Math.min(limit,eligible.length)} 项${result.complete ? "。" : "；分析中，排序会更新。"}` : "显示全部出牌，包括增加向听的方案。"));
    if (actual !== null && !shown.some(row => row.discard === actual)) {
      const played = ordered.find(row => row.discard === actual);
      if (played) { root.append(element("p", "calc-hint", "另列本局实际打出的牌，便于比较。")); shown.push(played); }
    }
    for (const row of shown) {
      const details = element("details", "study-option"); details.dataset.discard = row.discard;
      if (row.discard === actual) details.classList.add("study-actual");
      const heading = element("summary");
      heading.append(handCalculator.makeTile(row.discard), element("strong", "", `打 ${row.discard}`),
        element("span", "", handCalculator.shantenText(row.shanten)), element("span", "", row.eligible === false ? "增加向听" : this.score(row.value)));
      details.append(heading); root.append(details);
      details.ontoggle = () => {
        if (!details.open || details.dataset.built) return;
        details.dataset.built = "true";
        const body = element("div", "study-option-body"), after = [...command.hand]; after.splice(after.indexOf(row.discard), 1);
        const snapshot = { ...command, hand: after, visible: [...command.visible, row.discard],
          context: { self_draw_allowed: false, is_heavenly: false } };
        delete snapshot.winning_tile;
        if (row.eligible !== false) this.improvements(body, row.improvements, snapshot);
        else {
          body.append(element("p", "calc-hint", "这个方案的向听较高，可单独计算其后续进张。"));
          const button = element("button", "secondary-button", "单独分析这条路线"); body.append(button);
          button.onclick = async () => {
            button.disabled = true;
            try { const r = await handCalculator.request({ ...snapshot, quick: true }); this.renderAnalysis(r, snapshot, body); }
            catch (error) { button.disabled = false; button.textContent = error.message + "，点击重试"; }
          };
        }
        details.append(body);
      };
    }
  },

  score(value) { return typeof value === "number" ? handCalculator.format(value) : "计算中…"; },

  improvements(root, rows, command) {
    root.append(element("p", "calc-hint", `${rows.length} 种进张 · ${rows.reduce((n,r) => n + r.remaining_count, 0)} 张未见副本；展开查看贡献和分组。`));
    for (const row of rows) {
      const details = element("details", "study-option"), summary = element("summary"); details.dataset.incoming = row.tile;
      summary.append(handCalculator.makeTile(row.tile), element("strong", "", `摸 ${row.tile}`),
        element("span", "", row.empty ? "空听" : `${row.remaining_count} 张`), element("span", "", `贡献 ${this.score(row.contribution)}`));
      details.append(summary); root.append(details);
      details.ontoggle = async () => {
        if (!details.open || details.dataset.loading || details.dataset.loaded) return;
        details.dataset.loading = "true";
        let body = details.querySelector(".study-option-body");
        if (!body) { body = element("div", "study-option-body"); details.append(body); }
        body.replaceChildren();
        if (!row.win) {
          body.append(element("p", "calc-hint", `进入 ${handCalculator.shantenText(row.next_shanten)}；后续最优评价 ${this.score(row.value)}。`));
          details.dataset.loaded = "true"; delete details.dataset.loading; return;
        }
        handCalculator.renderWin(body, row.win);
        const all = element("div", "study-all-solutions", "正在读取全部分组…"); body.append(all);
        try {
          const answer = await handCalculator.request({ ...command, action: "explain", tile: row.tile }); all.replaceChildren();
          answer.solutions.forEach((solution, index) => {
            const section = element("section", "practice-solution"); section.append(element("h3", "", `${solution.family} · 方案 ${index + 1}`));
            handCalculator.renderGroups(section, solution.groups); all.append(section);
          });
          details.dataset.loaded = "true";
        } catch (error) { all.textContent = `${error.message}；收起后展开可重试。`; }
        finally { delete details.dataset.loading; }
      };
    }
  },

  replayError(message = "") { $("replay-error").textContent = message; $("replay-error").hidden = !message; },
  async refresh(preferred = this.replayId) {
    if (this.loading) return;
    this.loading = true; this.replayError();
    try {
      const response = await fetch("/api/replays"), data = await response.json();
      if (!response.ok) throw new Error(data.error);
      const select = $("replay-select"); select.replaceChildren();
      if (!data.replays.length) select.append(new Option("暂无牌谱，可先打一局或导入文件", ""));
      for (const item of data.replays) {
        const option = new Option(`第 ${item.round_number} 局 · ${item.experience === "teaching" ? "教学" : "普通"} · ${item.steps} 步${item.complete ? "" : " · 进行中"}`, item.id);
        option.disabled = !item.complete; select.append(option);
      }
      const chosen = data.replays.find(r => r.id === preferred && r.complete) || data.replays.find(r => r.complete);
      select.value = chosen?.id || "";
      if (chosen && chosen.id !== this.replayId) await this.load(chosen.id);
    } catch (error) { this.replayError(error.message); }
    finally { this.loading = false; }
  },
  async load(identifier) {
    if (!identifier) return;
    this.stop(false); this.replayError();
    try {
      const response = await fetch(`/api/replays/${encodeURIComponent(identifier)}`), data = await response.json();
      if (!response.ok) throw new Error(data.error);
      this.replay = data.replay; this.replayId = identifier; this.index = 0;
      $("replay-select").value = identifier; this.renderReplay(); this.sync();
    } catch (error) { this.replayError(error.message); }
  },
  async importFile() {
    const file = $("replay-file").files[0]; if (!file) return;
    this.stop(false); this.replayError();
    try {
      if (file.size > 16 * 1024 * 1024) throw new Error("牌谱文件须小于 16 MB");
      const parsed = JSON.parse(await file.text());
      const response = await fetch("/api/replays/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed) });
      const data = await response.json(); if (!response.ok) throw new Error(data.error);
      this.replay = data.replay; this.replayId = data.id; this.index = 0;
      await this.refresh(data.id); this.renderReplay(); this.sync();
    } catch (error) { this.replayError(error.message || "牌谱导入失败"); }
    finally { $("replay-file").value = ""; }
  },
  save() {
    if (!this.replay) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(this.replay)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url;
    link.download = `算术麻将-第${this.replay.round_number}局-${this.replay.round_id.slice(0,8)}.json`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  seek(index) {
    if (!this.replay) return;
    this.index = Math.max(0, Math.min(this.replay.frames.length - 1, index));
    this.renderReplay(); this.sync();
  },
  play() {
    if (!this.replay || this.playback) return;
    clearTimeout(this.timer);
    if (this.index === this.replay.frames.length - 1) this.seek(0);
    clearTimeout(this.timer);
    this.playback = setInterval(() => {
      this.seek(this.index + 1);
      if (this.index === this.replay.frames.length - 1) this.stop();
    }, Number($("replay-speed").value));
    $("replay-play").textContent = "暂停";
  },
  stop(analyze = true) {
    clearInterval(this.playback); this.playback = null;
    $("replay-play").textContent = "播放";
    if (analyze && $("study-auto").checked) this.schedule();
  },
  renderReplay() {
    if (!this.replay) return;
    const frames = this.replay.frames, frame = frames[this.index], viewer = $("replay-viewer").value;
    for (const id of ["replay-save", "replay-play", "replay-progress"]) $(id).disabled = false;
    $("replay-prev").disabled = this.index === 0; $("replay-next").disabled = this.index === frames.length - 1;
    $("replay-progress").max = frames.length - 1; $("replay-progress").value = this.index;
    $("replay-step").textContent = `${this.index + 1} / ${frames.length}`;
    $("replay-event").textContent = `${frame.label} · 牌山 ${frame.wall_count} 张`;
    const root = $("replay-board"); root.replaceChildren();
    const seat = frame.players.findIndex(p => p.player_id === viewer);
    for (let offset = 0; offset < 4; offset++) {
      const player = frame.players[(seat + offset) % 4], section = element("section", "replay-player"); section.dataset.playerId = player.player_id;
      section.classList.toggle("active", player.seat === frame.current_turn_seat);
      section.append(element("h2", "", `${player.name}${player.is_winner ? " · 已胡" : ""} · ${player.score} 分`));
      const hand = element("div", "replay-tiles");
      const reveal = player.player_id === viewer || $("replay-reveal").checked;
      player.hand_tile_records.forEach(record => {
        const node = reveal ? handCalculator.makeTile(record.value) : element("span", "preview-card-back");
        if (reveal && record.tile_id === frame.drawn_tile_id) node.classList.add("practice-winning");
        hand.append(node);
      });
      section.append(hand);
      for (const meld of player.melded_groups) {
        const group = element("div", "replay-tiles replay-meld"); group.append(element("span", "", meld.group_type));
        meld.tiles.forEach(value => group.append(handCalculator.makeTile(value))); section.append(group);
      }
      section.append(element("p", "calc-hint", `弃牌 · ${player.discarded_tiles.length} 张`));
      const river = element("div", "replay-tiles replay-river"); player.discarded_tiles.forEach(value => river.append(handCalculator.makeTile(value)));
      section.append(river); root.append(section);
    }
    for (const event of frame.settlement_events || []) {
      const section = element("section", "replay-player replay-settlement");
      this.settlement(section, event, frame);
      root.append(section);
    }
    $("replay-wall").hidden = !$("replay-wall-toggle").checked;
    $("replay-wall-tiles").replaceChildren();
    if ($("replay-wall-toggle").checked) frame.wall.forEach(value => $("replay-wall-tiles").append(handCalculator.makeTile(value)));
  },
};
