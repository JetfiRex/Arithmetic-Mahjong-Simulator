"use strict";

window.beginnerTutorial = {
  question: null, loading: false, handKey: null, visualKey: null, chaptersBuilt: false,
  nodes: new Map(), drag: null, touch: null, assistance: null, pendingAuto: false,

  initialize() {
    $("tutorial-previous").onclick = () => this.command("previous");
    $("tutorial-next").onclick = () => this.command(this.question?.next_action);
    $("tutorial-restart").onclick = () => this.command("restart");
    $("tutorial-hu").onclick = () => this.question?.won ? this.showResult() : this.command("hu");
    $("tutorial-reference").onclick = () => ruleReference.open("fans");
    $("tutorial-result-reference").onclick = () => ruleReference.open("fans");
    $("tutorial-result-continue").onclick = () => {
      $("tutorial-result-dialog").close();
      if (this.question?.next_action === "next" && this.question.next_enabled) this.command("next");
    };
    $("tutorial-blood-result").onclick = () => this.showBloodSettlement();
    $("tutorial-blood-continue").onclick = () => {
      $("tutorial-blood-dialog").close();
      if (this.question?.steps[this.question.step].visual === "blood_settlement" && this.question.next_enabled) this.command("next");
    };
    $("tutorial-assist").onclick = () => this.command("assist");
    for (const action of ["chi", "peng", "gang"]) $("tutorial-" + action).onclick = () => this.command(action);
    $("tutorial-hand").ondragover = event => { if (this.drag) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; this.previewDrop(event.clientX, event.clientY); } };
    $("tutorial-hand").ondrop = event => { if (this.drag) { event.preventDefault(); this.commitDrop(event.clientX, event.clientY); } };
    window.addEventListener("resize", () => { if (this.drag) this.drag.geometry = null; });
    window.addEventListener("scroll", () => { if (this.drag) this.drag.geometry = null; }, true);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.leave();
      else if (singlePlayer.currentMode === "tutorial") this.enter();
    });
  },
  active() { return singlePlayer.currentMode === "tutorial" && !document.hidden; },
  leave() { this.clearDrag(); this.clearAssistance(); },
  error(message = "") { $("tutorial-error").textContent = message; $("tutorial-error").hidden = !message; },
  async enter() {
    if (this.loading) return;
    this.loading = true; this.error(); this.renderControls();
    try {
      const response = await fetch("/api/tutorial", { cache: "no-store" }), payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "示范局准备失败");
      this.question = payload.tutorial; this.render();
    } catch (error) { this.error(error.message); }
    finally { this.loading = false; this.renderControls(); this.maybeAutoAssist(); }
  },
  async command(action, data = {}) {
    if (this.loading || !this.question) return;
    this.clearDrag();
    this.loading = true; this.error(); this.renderControls();
    try {
      const response = await fetch("/api/tutorial", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...data, tutorial_id: this.question.tutorial_id, revision: this.question.revision }) });
      const payload = await response.json();
      if (payload.tutorial) { this.question = payload.tutorial; this.render(); }
      if (!response.ok) throw new Error(payload.error || "教程操作失败");
      if (["restart", "select_lesson"].includes(action)) { $("tutorial-result-dialog").close(); $("tutorial-blood-dialog").close(); $("tutorial-text").focus({ preventScroll: true }); }
      if (action === "hu" && singlePlayer.currentMode === "tutorial") this.showResult();
      if (["next", "previous"].includes(action) && this.question?.steps[this.question.step].visual === "blood_settlement" && this.active()) this.showBloodSettlement();
    } catch (error) { this.error(error.message); }
    finally { this.loading = false; this.renderControls(); this.maybeAutoAssist(); }
  },

  renderControls() {
    const q = this.question, last = q && q.step === q.steps.length - 1;
    const chapter = ["第一步", "第二步", "第三步", "第四步", "第五步", "第六步"][q?.lesson || 0];
    const requirement = q?.interaction?.requirement;
    $("tutorial-restart").textContent = `重来${chapter}`;
    $("tutorial-restart").disabled = this.loading || !q;
    $("tutorial-previous").disabled = this.loading || !q || q.step === 0;
    $("tutorial-next").disabled = this.loading || !q?.next_enabled;
    $("tutorial-next").textContent = last ? `${chapter}${q.completed ? "已完成" : "完成"}` : q?.next_action === "draw" ? "摸一张牌" : q?.next_action === "regroup" ? "按传统麻将理牌" : "下一条";
    $("tutorial-hu").disabled = this.loading || !q?.hu_enabled;
    $("tutorial-board-actions").hidden = !!q?.display_only;
    $("tutorial-hu").textContent = q?.won ? "查看胡牌结果" : "胡";
    $("tutorial-hu").classList.toggle("tutorial-hu-ready", !!q?.hu_enabled && !q.won);
    $("tutorial-action-note").textContent = !q ? "正在准备示范局…" : q.won ? "已经胡牌，继续看看分组和讲解。" : q.hu_enabled ? "现在可以点击右边的“胡”了。" : q.drag ? "拖动亮起的牌；也可选中后按 Alt + 左右键，Alt + Home 移到目标开头。" : "跟随讲解，看看眼前这副牌。";
    $("tutorial-assist").hidden = !q?.drag;
    $("tutorial-assist").disabled = this.loading || !this.assistance || performance.now() < this.assistance.helpAt;
    $("tutorial-feedback").textContent = q?.notice || "";
    $("tutorial-feedback").hidden = !q?.notice || requirement === "chi_detail";
    $("tutorial-choice-feedback").textContent = q?.notice || "";
    $("tutorial-choice-feedback").hidden = !q?.notice || requirement !== "chi_detail";
    $("tutorial-assist-note").hidden = !q?.drag;
    $("tutorial-blood-result").hidden = !q?.blood_battle?.round_result;
    $("tutorial-blood-result").disabled = this.loading;
    for (const action of ["chi", "peng", "gang"]) {
      const button = $("tutorial-" + action);
      const ready = requirement === action || action === "chi" && requirement === "chi_blocked";
      button.hidden = q?.lesson !== 2; button.disabled = this.loading || !ready;
      button.classList.toggle("tutorial-action-ready", ready);
    }
    if (requirement) $("tutorial-action-note").textContent = requirement === "discard" ? `点击亮起的 ${q.interaction.discard} 出牌。` : requirement === "chi_detail" ? "在讲解区选择一种吃法，先想想哪种能保住刻子。" : `按提示点击右边的“${{peng:"碰", chi:"吃", chi_blocked:"吃", gang:"杠", hu:"胡"}[requirement]}”。`;
    for (const node of this.nodes.values()) {
      const id = node.dataset.tutorialTileId;
      node.draggable = this.canDrag(id);
      if (q?.lesson >= 2) { node.tabIndex = this.canDiscard(id) ? 0 : -1; node.setAttribute("aria-disabled", String(!this.canDiscard(id))); }
    }
    for (const button of $("tutorial-chi-choices").querySelectorAll("button")) button.disabled = this.loading;
    for (const button of $("tutorial-chapters").children) button.disabled = this.loading || !q?.available_lessons.includes(Number(button.dataset.lesson));
  },

  render() {
    const q = this.question; if (!q) return;
    const last = q.step === q.steps.length - 1;
    if (!this.chaptersBuilt) {
      q.chapters.forEach((title, index) => {
        const button = element("button", "tutorial-chapter");
        button.append(element("span", "", String(index + 1)), document.createTextNode(title));
        button.dataset.lesson = index;
        button.onclick = () => this.command("select_lesson", { lesson: index });
        if (!q.available_lessons.includes(index)) button.title = "后续课程准备中";
        $("tutorial-chapters").append(button);
      });
      this.chaptersBuilt = true;
    }
    for (const button of $("tutorial-chapters").children) {
      const lesson = Number(button.dataset.lesson);
      if (lesson === q.lesson) button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current");
      button.classList.toggle("tutorial-chapter-completed", q.completed_lessons.includes(lesson));
    }
    const step = q.steps[q.step];
    $("tutorial-text").textContent = step.text;
    $("tutorial-completed").hidden = !q.completed;
    $("tutorial-progress-label").textContent = `${q.step + 1} / ${q.steps.length}`;
    $("tutorial-next-note").textContent = !q.next_enabled && !last ? q.drag ? "整理好当前这一组，再继续。" : q.interaction ? "先完成当前提示的操作，再继续。" : "先点击牌桌右下角的“胡”，再继续。" : q.lesson === 0 ? "第二步已开放：点击上方“胡牌”，学习拖牌与不同听法。" : q.lesson === 1 ? "第三步已开放：点击上方“互动：吃碰杠”，练习鸣牌。" : q.lesson === 2 ? "第四步已开放：点击上方“传统麻将”，换个角度看牌。" : q.lesson === 3 ? "第五步已开放：点击上方“七小对和清龙”，看看特殊胡法。" : q.lesson === 4 ? "第六步已开放：点击上方“血战”，学习胡牌后的继续对局与赔付。" : "六步教程结束，可以回到首页开始普通模式或教学模式。";
    $("tutorial-next-note").hidden = q.next_enabled && !last;
    $("tutorial-hand-label").textContent = q.display_only ? `${q.example.name}示例` : q.blood_battle && q.won ? "我的已胡手牌" : "我的手牌";
    $("tutorial-opponents").hidden = !!q.display_only;
    $("tutorial-hand-count").textContent = `${q.hand.length} 张`;
    $("tutorial-hand").setAttribute("aria-label", `固定的 ${q.hand.length} 张示范手牌`);
    this.renderHand();
    document.querySelector(".tutorial-board").classList.toggle("tutorial-interaction-board", !!q.interaction);
    const opponentsKey = q.tutorial_id + JSON.stringify(q.opponents);
    if ($("tutorial-opponents").dataset.content !== opponentsKey) {
      $("tutorial-opponents").dataset.content = opponentsKey; $("tutorial-opponents").replaceChildren();
      for (const opponent of q.opponents) {
        const block = element("div", "tutorial-opponent"); block.append(element("span", "", opponent.name + (q.interaction ? ` · 暗手 ${opponent.hand_count} 张` : "")));
        if (q.blood_battle) {
          block.classList.toggle("tutorial-opponent-out", opponent.is_out);
          block.append(element("span", "tutorial-opponent-status", opponent.is_out ? `第 ${opponent.win_order} 位已胡退出` : "仍在打牌"));
        }
        const backs = element("div", "tutorial-mini-backs");
        for (let i = 0; i < opponent.hand_count; i++) backs.append(element("span", "mini-back"));
        block.append(backs);
        if (q.interaction) {
          const melds = element("div", "tutorial-opponent-melds"); this.renderMelds(melds, opponent.melded_groups); block.append(melds);
          const river = element("div", "tutorial-river"); river.setAttribute("aria-label", opponent.name + "弃牌");
          river.append(element("span", "", "弃牌"), ...opponent.discarded_tiles.map(v => handCalculator.makeTile(v))); block.append(river);
        }
        $("tutorial-opponents").append(block);
      }
    }
    this.renderInteraction();
    this.renderBlood();
    for (const node of $("tutorial-hand").querySelectorAll("[data-tutorial-tile]")) {
      const active = q.highlight.includes(Number(node.dataset.tutorialTile));
      node.classList.toggle("tutorial-lit", active);
      node.classList.toggle("tutorial-dim", q.highlight.length > 0 && !active);
    }
    for (const group of $("tutorial-hand").children) {
      const label = q.groups[Number(group.dataset.group)].label;
      group.querySelector(".tutorial-group-caption").textContent = label;
      group.classList.toggle("tutorial-group-known", !!label);
    }
    const visualKey = `${q.tutorial_id}:${q.step}`;
    if (visualKey !== this.visualKey) { this.visualKey = visualKey; this.renderVisual(step); }
    const showFans = ["fans", "traditional_fans"].includes(step.visual);
    $("tutorial-fans").hidden = !showFans;
    $("tutorial-reference").hidden = !showFans;
    if (showFans && q.result) this.renderFans($("tutorial-fans"));
    this.armAssistance();
    this.renderControls();
  },

  renderHand() {
    if (this.drag) return;
    const q = this.question, root = $("tutorial-hand");
    root.classList.toggle("tutorial-sorting-hand", q.lesson >= 1);
    root.classList.toggle("tutorial-flat-hand", q.lesson === 1 && q.step === q.steps.length - 1);
    const key = q.hand.map(t => t.tile_id).join(",") + ":" + q.groups.map(g => `${g.start}-${g.end}`).join(",");
    if (key !== this.handKey) {
      this.handKey = key;
      const live = new Set(q.hand.map(t => t.tile_id));
      for (const id of this.nodes.keys()) if (!live.has(id)) this.nodes.delete(id);
      const blocks = q.groups.map((group, index) => {
        const block = element("div", "tutorial-card-group"); block.dataset.group = index;
        block.style.setProperty("--cards", group.end - group.start);
        q.hand.slice(group.start, group.end).forEach(record => {
          let node = this.nodes.get(record.tile_id);
          if (!node) { node = this.makeHandTile(record, [1,2].includes(q.lesson)); this.nodes.set(record.tile_id, node); }
          block.append(node);
        });
        block.append(element("span", "tutorial-group-caption")); return block;
      });
      root.replaceChildren(...blocks);
    }
    q.hand.forEach((record, index) => {
      const node = this.nodes.get(record.tile_id);
      node.dataset.tutorialTile = index;
      node.setAttribute("aria-label", `第 ${index + 1} 张，${record.value}，牌池共有 ${handCalculator.supply[VALUES.indexOf(record.value)]} 张`);
      node.classList.toggle("tutorial-draggable", this.eligible(record.tile_id));
      node.classList.toggle("tutorial-drawn", q.lesson >= 1 && record.tile_id === q.drawn_tile_id);
      node.classList.toggle("tutorial-can-discard", q.interaction?.requirement === "discard" && record.value === q.interaction.discard);
      node.title = q.interaction?.requirement === "discard" && record.value === q.interaction.discard ? `点击打出 ${record.value}` : this.eligible(record.tile_id) ? "拖动整理；Alt + 左右键移动，Alt + Home 移到目标开头" : "跟随当前讲解";
    });
  },
  eligible(id) {
    const q = this.question, index = q?.hand.findIndex(t => t.tile_id === id);
    return !!q?.drag && index >= q.drag.locked && q.drag.allowed_values.includes(q.hand[index]?.value);
  },
  canDrag(id) { return !this.loading && this.active() && this.eligible(id); },
  canDiscard(id) {
    return !this.loading && this.active() && this.question?.interaction?.requirement === "discard" && this.question.hand.some(t => t.tile_id === id && t.value === this.question.interaction.discard);
  },
  makeHandTile(record, interactive) {
    const id = record.tile_id, node = handCalculator.makeTile(record.value, interactive);
    node.dataset.tutorialTileId = id;
    if (!interactive) return node;
    node.onclick = () => { if (this.canDiscard(id)) this.command("discard", { tile_id:id }); };
    node.ondragstart = event => {
      if (!this.canDrag(id)) { event.preventDefault(); return; }
      this.drag = { id, tutorialId: this.question.tutorial_id, step: this.question.step, geometry: null };
      event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", id);
      node.classList.add("dragging");
    };
    node.ondragend = () => { this.clearDrag(); this.maybeAutoAssist(); };
    node.onkeydown = event => {
      if (!event.altKey || !["Home", "ArrowLeft", "ArrowRight"].includes(event.key) || !this.canDrag(id)) return;
      event.preventDefault();
      const q = this.question, position = q.hand.findIndex(t => t.tile_id === id);
      const last = q.drag.kind === "quad" ? q.drag.locked : q.hand.length - 1;
      const index = event.key === "Home" ? q.drag.target : Math.max(q.drag.target, Math.min(last, position + (event.key === "ArrowLeft" ? -1 : 1)));
      this.command("move", { tile_id: id, index }).then(() => { if (this.active()) node.focus({ preventScroll: true }); });
    };
    node.onpointerdown = event => {
      if (event.pointerType === "mouse" || !this.canDrag(id)) return;
      this.touch = { id, pointerId: event.pointerId, x: event.clientX, y: event.clientY };
      node.setPointerCapture(event.pointerId);
    };
    node.onpointermove = event => {
      if (this.touch?.id !== id || this.touch.pointerId !== event.pointerId) return;
      if (!this.drag && Math.hypot(event.clientX - this.touch.x, event.clientY - this.touch.y) < 7) return;
      event.preventDefault();
      if (!this.drag) { this.drag = { id, tutorialId: this.question.tutorial_id, step: this.question.step, geometry: null }; node.classList.add("dragging"); }
      this.previewDrop(event.clientX, event.clientY);
    };
    node.onpointerup = event => {
      if (this.touch?.id !== id) return;
      if (this.drag) this.commitDrop(event.clientX, event.clientY);
      else { this.clearDrag(); this.maybeAutoAssist(); }
    };
    node.onpointercancel = event => { if (this.touch?.pointerId === event.pointerId) { this.clearDrag(); this.maybeAutoAssist(); } };
    return node;
  },
  dropTarget(x, y, fresh = false) {
    if (!this.drag) return null;
    if (fresh) this.drag.geometry = null;
    if (!this.drag.geometry) this.drag.geometry = {
      bounds: $("tutorial-hand").getBoundingClientRect(),
      targets: [...$("tutorial-hand").querySelectorAll("[data-tutorial-tile-id]")].filter(n => n.dataset.tutorialTileId !== this.drag.id).map(node => node.getBoundingClientRect()),
    };
    const { bounds, targets } = this.drag.geometry;
    if (x < bounds.left - 8 || x > bounds.right + 8 || y < bounds.top - 8 || y > bounds.bottom + 8) return null;
    let index = targets.findIndex(r => y < r.top || y <= r.bottom && x < (r.left + r.right) / 2);
    if (index < 0) index = targets.length;
    const rect = targets[index] || targets.at(-1);
    return { index, left: (targets[index] ? rect.left - 4 : rect.right + 3) - bounds.left, top: rect.top - bounds.top, height: rect.height };
  },
  previewDrop(x, y) {
    const target = this.dropTarget(x, y), root = $("tutorial-hand");
    let marker = root.querySelector(".drop-slot");
    if (!target) { marker?.remove(); return; }
    if (!marker) { marker = element("span", "drop-slot"); root.append(marker); this.drag.markerKey = null; }
    const css = `left:${target.left}px;top:${target.top}px;height:${target.height}px;bottom:auto`;
    if (this.drag.markerKey !== css) { marker.style.cssText = css; this.drag.markerKey = css; }
  },
  commitDrop(x, y) {
    const drag = this.drag; if (!drag) return;
    const target = this.dropTarget(x, y, true); this.clearDrag();
    if (target && drag.tutorialId === this.question?.tutorial_id && drag.step === this.question.step)
      this.command("move", { tile_id: drag.id, index: target.index });
    else this.maybeAutoAssist();
  },
  clearDrag() {
    if (this.touch) {
      const node = this.nodes.get(this.touch.id), pointer = this.touch.pointerId;
      if (node?.hasPointerCapture(pointer)) node.releasePointerCapture(pointer);
    }
    this.drag = null; this.touch = null;
    $("tutorial-hand").querySelector(".drop-slot")?.remove();
    for (const node of this.nodes.values()) node.classList.remove("dragging");
  },
  clearAssistance() {
    if (this.assistance) { clearTimeout(this.assistance.helpTimer); clearTimeout(this.assistance.autoTimer); }
    this.assistance = null; this.pendingAuto = false;
  },
  armAssistance() {
    const q = this.question, key = `${q.tutorial_id}:${q.step}`;
    if (!q.drag || !this.active()) { this.clearAssistance(); return; }
    if (this.assistance?.key === key) return;
    this.clearAssistance();
    const helpMs = q.drag.help_after * 1000, autoMs = Math.max(1000, q.drag.auto_after * 1000);
    this.assistance = { key, helpAt: performance.now() + helpMs,
      helpTimer: setTimeout(() => this.renderControls(), helpMs),
      autoTimer: setTimeout(() => { this.pendingAuto = true; this.maybeAutoAssist(); }, autoMs) };
  },
  maybeAutoAssist() {
    if (this.pendingAuto && this.active() && this.question?.drag && !this.loading && !this.drag && !this.touch) {
      this.pendingAuto = false; this.command("assist", { automatic: true });
    }
  },

  renderMelds(root, melds) {
    for (const meld of melds) {
      const block = element("div", "tutorial-meld-block"), cards = element("div", "meld tutorial-meld");
      for (const face of meld.display_tiles || meld.tiles.map(value => ({ value }))) {
        const node = handCalculator.makeTile(face.value);
        if (face.orientation === "sideways") node.classList.add("sideways");
        if (face.orientation === "back") { node.classList.add("back"); node.setAttribute("aria-label", "背面牌"); }
        cards.append(node);
      }
      block.append(cards, element("span", "tutorial-meld-label", meld.group_type)); root.append(block);
    }
  },
  renderInteraction() {
    const q = this.question, data = q.interaction;
    $("tutorial-live-table").hidden = !data;
    $("tutorial-meld-area").hidden = !data?.melds.length;
    $("tutorial-chi-choices").hidden = !data?.chi_options.length;
    const key = q.tutorial_id + JSON.stringify(data);
    if (this.interactionKey === key) return;
    this.interactionKey = key;
    $("tutorial-chi-choices").replaceChildren(); $("tutorial-meld-area").replaceChildren(); $("tutorial-my-river").replaceChildren();
    if (!data) return;
    $("tutorial-wall-count").textContent = `牌山剩余 ${data.wall_remaining} 张 · 已出牌 ${data.history_count} 次`;
    $("tutorial-my-river").append(element("span", "", "我的弃牌"), ...q.my_discards.map(v => handCalculator.makeTile(v)));
    if (data.melds.length) {
      $("tutorial-meld-area").append(element("strong", "tutorial-meld-heading", "我的副露"));
      const groups = element("div", "tutorial-meld-list"); this.renderMelds(groups, data.melds); $("tutorial-meld-area").append(groups);
    }
    for (const option of data.chi_options) {
      const button = element("button", "tutorial-chi-option"); button.dataset.optionId = option.option_id;
      const values = displayFormula(option.display_tiles);
      button.append(element("strong", "", `${values[0]} ${values[1]} ${values[2]} = ${values[3]}`));
      const cards = element("div", "tutorial-choice-tiles");
      option.display_tiles.forEach((value, index) => {
        const node = handCalculator.makeTile(value);
        if (index === option.claimed_display_index) node.classList.add("practice-winning");
        cards.append(node);
      });
      button.append(cards, element("span", "", "点击选择 · 金框是上家打出的 2"));
      button.onclick = () => this.command("chi_detail", { option_id:option.option_id }); $("tutorial-chi-choices").append(button);
    }
  },

  bloodName(playerId) { return this.question.blood_battle.players.find(p => p.player_id === playerId)?.name || "玩家"; },
  bloodScore(value) { return value > 0 ? `+${value}` : String(value); },
  bloodEventCard(event) {
    const card = element("section", "tutorial-blood-event");
    card.dataset.winnerId = event.winner_id;
    card.append(element("strong", "", `第 ${event.win_order} 位 · ${this.bloodName(event.winner_id)}自摸`));
    const win = event.win_result;
    card.append(element("p", "calc-hint", `${win.total_fan} 番，按 ${win.capped_fan} 番结算 · 每家赔付 ${win.base_payment}`));
    for (const delta of event.score_deltas) {
      const row = element("div", "tutorial-blood-payment"); row.dataset.playerId = delta.player_id; row.dataset.delta = delta.delta;
      row.append(element("span", "", this.bloodName(delta.player_id)), element("strong", delta.delta > 0 ? "tutorial-score-gain" : "tutorial-score-loss", this.bloodScore(delta.delta))); card.append(row);
    }
    if (event.winner_id !== "p0" && !event.score_deltas.some(d => d.player_id === "p0")) card.append(element("p", "calc-hint tutorial-blood-exempt", "你已先胡，本次不赔付。"));
    if (event.winner_id === "p2") {
      const details = element("details", "tutorial-blood-hand"); details.append(element("summary", "", "查看对家胡牌分组"));
      details.ontoggle = () => {
        if (!details.open || details.dataset.rendered) return;
        details.dataset.rendered = "true";
        const groups = win.groups.map(displayFormula), winningIndex = groups.flat().lastIndexOf(win.winning_tile); let index = 0;
        handCalculator.renderGroups(details, groups.map(values => ({label:this.resultGroupLabel(values, win.win_type),tiles:values.map(value => ({value,winning:index++ === winningIndex}))})));
      };
      card.append(details);
    }
    return card;
  },
  renderBlood() {
    const q = this.question, data = q.blood_battle;
    $("tutorial-blood-panel").hidden = !data;
    $("tutorial-blood-payments").hidden = !data?.settlement_events.length;
    const key = q.tutorial_id + JSON.stringify(data && [data.players, data.settlement_events.map(e => e.settlement_id), !!data.round_result]);
    if (this.bloodKey === key) return;
    this.bloodKey = key; $("tutorial-blood-scores").replaceChildren(); $("tutorial-blood-payments").replaceChildren();
    if (!data) return;
    $("tutorial-blood-status").textContent = data.round_result ? "牌山摸空 · 流局 · 已胡两家" : `仍在打牌 ${data.active_player_ids.length} 人 · 已胡 ${data.settlement_events.length} 家`;
    for (const player of data.players) {
      const block = element("div", "tutorial-blood-seat"); block.dataset.playerId = player.player_id;
      block.append(element("span", "", player.name), element("small", "", player.is_out ? `第 ${player.win_order} 位已胡退出` : "仍在打牌"), element("strong", "", this.bloodScore(player.score))); $("tutorial-blood-scores").append(block);
    }
    for (const event of data.settlement_events) $("tutorial-blood-payments").append(this.bloodEventCard(event));
  },
  showBloodSettlement() {
    const q = this.question, data = q?.blood_battle, final = data?.round_result;
    if (!final || !this.active()) return;
    const root = $("tutorial-blood-result-content"); root.replaceChildren();
    root.append(element("p", "calc-hint", "牌山摸完，流局。两家已经胡牌，剩余两家未胡；此前的胡牌赔付保留。"));
    const table = element("table", "tutorial-blood-table"), heading = element("tr");
    for (const label of ["玩家", "本局收支", "结果"]) heading.append(element("th", "", label));
    const thead = element("thead"); thead.append(heading); table.append(thead);
    const body = element("tbody"), scores = new Map(final.score_deltas.map(d => [d.player_id, d.delta]));
    for (const player of final.final_player_states) {
      const row = element("tr"); row.dataset.playerId = player.player_id;
      row.append(element("td", "", this.bloodName(player.player_id)), element("td", "", this.bloodScore(scores.get(player.player_id))), element("td", "", player.is_winner ? `第 ${player.win_order} 位已胡` : "未胡")); body.append(row);
    }
    table.append(body); root.append(table);
    root.append(element("h3", "tutorial-fan-title", "逐次赔付"));
    for (const event of final.settlement_events) root.append(this.bloodEventCard(event));
    if (!$("tutorial-blood-dialog").open) $("tutorial-blood-dialog").showModal();
  },

  renderVisual(step) {
    const root = $("tutorial-example"); root.replaceChildren();
    if (["tile", "value", "stock"].includes(step.visual)) {
      const focus = element("div", `tutorial-enlarged tutorial-focus-${step.visual}`);
      focus.append(handCalculator.makeTile(step.tile)); root.append(focus);
    } else if (step.visual === "pool") {
      const button = element("button", "tutorial-board-link", "查看每种牌的张数 ↗");
      button.onclick = () => ruleReference.open("pool"); root.append(button);
    } else if (["traditional_regroup", "traditional_wait"].includes(step.visual)) {
      const waits = element("div", "tutorial-wait-tiles");
      for (const value of this.question.traditional.waits) waits.append(handCalculator.makeTile(value));
      waits.setAttribute("aria-label", "传统麻将待牌：二条、五条"); root.append(waits);
    } else if (["peng_offer", "chi_offer", "chi_again", "chi_blocked", "chi_choice", "traditional_ron"].includes(step.visual)) {
      const latest = this.question.interaction.last_discard;
      const discard = element("div", "tutorial-discard-preview");
      const card = handCalculator.makeTile(latest.tile);
      card.setAttribute("aria-label", `${["我", "下家", "对家", "上家"][latest.seat]}打出的 ${latest.tile}`);
      discard.append(card); root.append(discard);
    }
  },

  renderFans(root) {
    root.replaceChildren();
    const win = this.question.result.win_result;
    root.append(element("p", "calc-hint", `共 ${win.total_fan} 番，实际结算按 ${win.capped_fan} 番计算。`));
    for (const fan of win.fan_items) {
      const button = element("button", "tutorial-fan-row"); button.dataset.fan = fan.code;
      button.append(element("span", "", fan.name), element("strong", "", `${fan.total_fan} 番 ↗`));
      button.onclick = () => ruleReference.open("fans", fan.name); root.append(button);
    }
  },
  showResult() {
    if (!this.question?.result || singlePlayer.currentMode !== "tutorial") return;
    const root = $("tutorial-result-content"), win = this.question.result.win_result; root.replaceChildren();
    root.append(element("p", "tutorial-result-lead", `这副牌以“${win.win_type}”${win.win_method}胡牌。下面是它的分组：`));
    const original = this.question.hand.map(t => t.value);
    const groups = win.groups.map(displayFormula).sort((a,b) => original.indexOf(a[0]) - original.indexOf(b[0]));
    let index = 0; const winningIndex = groups.flat().lastIndexOf(win.winning_tile);
    handCalculator.renderGroups(root, groups.map(values => ({ label: this.resultGroupLabel(values, win.win_type),
      tiles: values.map(value => ({ value, winning: index++ === winningIndex })) })));
    root.append(element("h3", "tutorial-fan-title", "这手牌有哪些番？"));
    const fans = element("div", "tutorial-fans"); this.renderFans(fans); root.append(fans);
    if (this.question.blood_battle) root.append(this.bloodEventCard(this.question.result));
    const note = this.question.blood_battle ? "这是你本局的自摸赔付。你胡牌后退出，另外三家继续；他们后面的胡牌不再由你赔付。"
      : win.fan_items.some(f => f.code === "TIAN_HU") ? "本示范在自己第一次出牌前胡牌，因此也计天胡。番数表中可以查询各项说明。"
      : win.win_type === "传统麻将" ? "上家打出的 2 组成二三四条，这是点胡。传统麻将这一项加 1 番，另有全一位 2 番。手中保留原十三张牌，解答列出补入弃牌后的十四张分组。"
      : "本示范已经出过牌，也有吃碰副露，按实际局面计算番种。暗杠用了五张牌，所以完整牌组共有十五张。";
    root.append(element("p", "calc-hint", note));
    if (!$("tutorial-result-dialog").open) $("tutorial-result-dialog").showModal();
  },
  resultGroupLabel(values, winType) {
    if (values.length === 2) return "对子";
    if (values.every(v => v === values[0])) return values.length === 5 ? "暗杠 · 五张相同牌" : values.length === 3 ? "刻子 · 三张相同牌" : "刻子";
    if (winType === "传统麻将" && values.length === 3) return `顺子 · ${values.join("、")}条`;
    return `${values[0]} ${values[1]} ${values[2]} = ${values[3]}`;
  },
};
