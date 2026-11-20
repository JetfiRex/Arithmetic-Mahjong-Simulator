"use strict";

window.handCalculator = {
  tiles: [], visible: new Map(), nodes: new Map(), palette: new Map(), sequence: 0,
  supply: [4,4,6,6,6,4,6,4,4,4,4,2,4,2,4,4,4,2,4,2,4,4,8,12],
  inputMode: "hand", drawnId: null, busy: false, result: null, snapshot: null,
  requestChain: Promise.resolve(),
  drag: null, touch: null, suppressClickUntil: 0,

  initialize() {
    $("calc-analyze").onclick = () => this.analyze();
    $("calc-clear").onclick = () => {
      if (this.busy) return;
      this.tiles = []; this.visible.clear(); this.drawnId = null; this.inputMode = "hand"; this.changed();
    };
    $("calc-example").onclick = () => {
      if (this.busy) return;
      this.tiles = [1,"+",2,3,2,"+",2,4,3,"+",3,6,5].map(value => ({ id: ++this.sequence, value }));
      this.visible.clear(); this.drawnId = null; this.inputMode = "hand"; this.changed();
    };
    for (const mode of ["hand", "visible"]) $("calc-tab-" + mode).onclick = () => {
      this.inputMode = mode;
      if (mode === "visible") $("calc-visible-section").open = true;
      this.renderControls();
    };
    $("calc-min-fan").onchange = () => this.changed();
    $("calc-limit-draws").onchange = () => this.changed();
    $("calc-draws").onchange = () => this.changed();
    $("calc-drawn").onchange = () => { this.drawnId = Number($("calc-drawn").value); this.changed(); };
    $("calc-hand").ondragover = event => { if (this.drag) { event.preventDefault(); this.previewDrop(event.clientX, event.clientY); } };
    $("calc-hand").ondrop = event => { if (this.drag) { event.preventDefault(); this.commitDrop(event.clientX, event.clientY); } };
    this.renderHand(); this.renderControls();
  },

  enter() {
    if (!this.palette.size) for (const value of VALUES) {
      const button = this.makeTile(value, true);
      button.dataset.value = value;
      const badge = element("span", "calc-stock"); button.append(badge);
      button.onclick = () => this.add(value);
      this.palette.set(value, button); $("calc-palette").append(button);
    }
    this.renderControls();
  },
  makeTile(value, interactive = false) { const node = tile(value, interactive); node.classList.remove("number-view"); return node; },
  count(value) { return this.tiles.filter(item => item.value === value).length; },
  remaining(value) { return this.supply[VALUES.indexOf(value)] - this.count(value) - (this.visible.get(value) || 0); },
  error(message = "") { $("calc-error").textContent = message; $("calc-error").hidden = !message; },
  changed() {
    this.result = null; this.snapshot = null; this.error();
    this.renderHand(); this.renderVisible(); this.renderControls(); this.renderResult();
  },
  add(value) {
    if (this.busy || this.remaining(value) <= 0) return;
    if (this.inputMode === "visible") this.visible.set(value, (this.visible.get(value) || 0) + 1);
    else {
      if (this.tiles.length >= 14) return;
      const record = { id: ++this.sequence, value }; this.tiles.push(record);
      this.drawnId = this.tiles.length === 14 ? record.id : null;
    }
    this.changed();
  },
  remove(id) {
    if (this.busy || performance.now() < this.suppressClickUntil) return;
    this.tiles = this.tiles.filter(item => item.id !== id); this.drawnId = null; this.changed();
  },

  renderControls() {
    const count = this.tiles.length;
    $("calc-hand-count").textContent = `${count} / 13 或 14 张`;
    $("calc-tab-hand").setAttribute("aria-pressed", String(this.inputMode === "hand"));
    $("calc-tab-visible").setAttribute("aria-pressed", String(this.inputMode === "visible"));
    for (const [value, button] of this.palette) {
      const left = this.remaining(value);
      button.disabled = this.busy || left <= 0 || this.inputMode === "hand" && count >= 14;
      button.querySelector(".calc-stock").textContent = left;
      button.title = `${value} · 还可录入 ${left} 张`;
      button.setAttribute("aria-label", button.title);
    }
    for (const id of ["calc-clear", "calc-example", "calc-min-fan", "calc-limit-draws", "calc-drawn"]) $(id).disabled = this.busy;
    $("calc-draws").disabled = this.busy || !$("calc-limit-draws").checked;
    $("calc-analyze").disabled = this.busy || ![13, 14].includes(count);
    $("calc-analyze").textContent = this.busy ? "正在分析…" : this.result && !this.result.complete ? "继续分析" : "分析这副手牌";
    $("calc-status").textContent = this.busy ? "分析中，可以返回首页；牌面会保留" : this.result ? this.result.complete ? "分析完成" : "部分分析完成，可继续计算" : [13,14].includes(count) ? count === 13 ? "13 张：分析待牌与有效进张" : "14 张：比较各出牌方案" : "录入 13 或 14 张后开始分析";
    $("calc-drawn-setting").hidden = count !== 14;
    const select = $("calc-drawn");
    const optionKey = this.tiles.map(item => `${item.id}:${item.value}`).join(",");
    if (select.dataset.key !== optionKey) {
      select.replaceChildren();
      this.tiles.forEach((item, index) => {
        const option = element("option", "", `${item.value}（第 ${index + 1} 张）`); option.value = item.id; select.append(option);
      });
      select.dataset.key = optionKey;
    }
    if (this.drawnId !== null) select.value = this.drawnId;
    for (const node of this.nodes.values()) node.disabled = this.busy;
  },

  renderHand() {
    if (this.drag) return;
    const root = $("calc-hand"), current = new Set(this.tiles.map(item => item.id));
    for (const [id, node] of this.nodes) if (!current.has(id)) { node.remove(); this.nodes.delete(id); }
    root.querySelectorAll(".preview-card-slot").forEach(node => node.remove());
    this.tiles.forEach((item, index) => {
      let node = this.nodes.get(item.id);
      if (!node) {
        node = this.makeTile(item.value, true); node.dataset.calcTileId = item.id;
        node.title = `${item.value} · 点击移除，拖动排序`; node.setAttribute("aria-label", node.title);
        node.onclick = () => this.remove(item.id);
        node.ondragstart = event => {
          if (this.busy) { event.preventDefault(); return; }
          this.drag = { id: item.id }; node.classList.add("dragging");
          event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", String(item.id));
        };
        node.ondragend = () => this.clearDrag();
        node.onkeydown = event => {
          if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
          event.preventDefault();
          this.move(item.id, this.tiles.findIndex(t => t.id === item.id) + (event.key === "ArrowLeft" ? -1 : 1)); node.focus();
        };
        node.onpointerdown = event => {
          if (event.pointerType === "mouse" || this.busy) return;
          this.touch = { id: item.id, pointerId: event.pointerId, x: event.clientX, y: event.clientY }; node.setPointerCapture(event.pointerId);
        };
        node.onpointermove = event => {
          if (this.touch?.id !== item.id || this.touch.pointerId !== event.pointerId) return;
          if (!this.drag && Math.hypot(event.clientX - this.touch.x, event.clientY - this.touch.y) < 7) return;
          event.preventDefault();
          if (!this.drag) { this.drag = { id: item.id }; node.classList.add("dragging"); }
          this.previewDrop(event.clientX, event.clientY);
        };
        node.onpointerup = event => {
          if (this.touch?.pointerId !== event.pointerId) return;
          if (this.drag) { event.preventDefault(); this.commitDrop(event.clientX, event.clientY); }
          else this.touch = null;
        };
        node.onpointercancel = event => { if (this.touch?.pointerId === event.pointerId) this.clearDrag(); };
        this.nodes.set(item.id, node);
      }
      node.draggable = !this.busy; node.disabled = this.busy;
      node.classList.toggle("practice-winning", this.tiles.length === 14 && item.id === this.drawnId);
      if (root.children[index] !== node) root.insertBefore(node, root.children[index] || null);
    });
    if (!this.tiles.length) for (let i = 0; i < 14; i++) root.append(element("span", "preview-card-slot", i + 1));
  },

  renderVisible() {
    const root = $("calc-visible"); root.replaceChildren();
    let total = 0;
    for (const value of VALUES) {
      const count = this.visible.get(value) || 0; total += count;
      if (!count) continue;
      const wrap = element("div", "calc-visible-item"), button = this.makeTile(value, true);
      button.disabled = this.busy; button.title = `移除一张 ${value}`;
      button.onclick = () => { if (this.busy) return; this.visible.set(value, count - 1); this.changed(); };
      wrap.append(button, element("span", "", `× ${count}`)); root.append(wrap);
    }
    $("calc-visible-count").textContent = `${total} 张`;
    if (!total) root.append(element("span", "calc-hint", "尚未录入其他已见牌"));
  },

  dropTarget(x, y) {
    const root = $("calc-hand"), bounds = root.getBoundingClientRect();
    const targets = [...root.querySelectorAll("[data-calc-tile-id]")].filter(node => Number(node.dataset.calcTileId) !== this.drag.id)
      .map(node => node.getBoundingClientRect());
    if (!targets.length) return { index: 0, left: 0, top: 0, height: bounds.height };
    let index = targets.findIndex(rect => y < rect.top || y <= rect.bottom && x < (rect.left + rect.right) / 2);
    if (index < 0) index = targets.length;
    const rect = targets[index] || targets.at(-1);
    return { index, left: (targets[index] ? rect.left - 3 : rect.right + 3) - bounds.left, top: rect.top - bounds.top, height: rect.height };
  },
  previewDrop(x, y) {
    const target = this.dropTarget(x, y), root = $("calc-hand");
    let marker = root.querySelector(".drop-slot");
    if (!marker) { marker = element("span", "drop-slot"); root.append(marker); }
    const css = `left:${target.left}px;top:${target.top}px;height:${target.height}px;bottom:auto`;
    if (marker.dataset.position !== css) { marker.style.cssText = css; marker.dataset.position = css; }
  },
  commitDrop(x, y) {
    const id = this.drag.id, target = this.dropTarget(x, y);
    this.clearDrag(false); this.suppressClickUntil = performance.now() + 350; this.move(id, target.index);
  },
  move(id, target) {
    const from = this.tiles.findIndex(item => item.id === id);
    if (from < 0) return;
    const [item] = this.tiles.splice(from, 1); this.tiles.splice(Math.max(0, Math.min(this.tiles.length, target)), 0, item);
    this.renderHand(); this.renderControls();
  },
  clearDrag(render = true) {
    this.drag = null; this.touch = null;
    $("calc-hand").querySelector(".drop-slot")?.remove();
    for (const node of this.nodes.values()) node.classList.remove("dragging");
    if (render) this.renderHand();
  },

  request(body) {
    // Expanding a wait fetches its groups. A simultaneous "simulate draw"
    // waits for that request rather than racing the service's single worker.
    const request = this.requestChain.then(async () => {
      const response = await fetch("/api/calculator", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json();
      if (!response.ok) { const error = new Error(result.error || "分析失败，请重试"); error.status = response.status; throw error; }
      return result.analysis;
    });
    this.requestChain = request.catch(() => {});
    return request;
  },
  async analyze() {
    if (this.busy || ![13,14].includes(this.tiles.length) || !$("calc-min-fan").reportValidity()
      || $("calc-limit-draws").checked && !$("calc-draws").reportValidity()) return;
    const visible = VALUES.flatMap(value => Array(this.visible.get(value) || 0).fill(value));
    const body = { hand: this.tiles.map(item => item.value), visible, min_fan: Number($("calc-min-fan").value),
      draws_left: $("calc-limit-draws").checked ? Number($("calc-draws").value) : null };
    if (this.tiles.length === 14) body.winning_tile = this.tiles.find(item => item.id === this.drawnId)?.value ?? this.tiles.at(-1).value;
    this.busy = true; this.error(); this.renderControls(); this.renderVisible(); this.renderHand();
    try { this.result = await this.request(body); this.snapshot = body; this.renderResult(); }
    catch (error) { this.error(error.message); }
    finally { this.busy = false; this.renderControls(); this.renderVisible(); this.renderHand(); }
  },
  format(value) { return value === null || value === undefined ? "未完成" : value !== 0 && Math.abs(value) < .0001 ? value.toExponential(3) : value.toLocaleString("zh-CN", { maximumFractionDigits: 6 }); },
  shantenText(value) { return value === null ? "计算中" : value < 0 ? "已成牌" : value === 0 ? "听牌" : `${value} 向听`; },

  renderResult() {
    const root = $("calc-results"); root.replaceChildren();
    const r = this.result;
    $("calc-completeness").textContent = r ? r.complete ? "已完成" : "部分完成" : "等待分析";
    $("calc-shanten").textContent = r ? r.shanten === null && r.shanten_bounds ? `${r.shanten_bounds[0]}～${r.shanten_bounds[1] ?? "?"}` : this.shantenText(r.shanten) : "—";
    $("calc-value").textContent = r ? this.format(r.value) : "—";
    $("calc-unseen").textContent = r ? `${r.unseen_total}` : "—";
    $("calc-result-note").textContent = !r ? "输入手牌后，这里会展示向听、进张、出牌评分及成牌分组。" : r.message || (r.hand_size === 13
      ? r.shanten === 0 ? "以下列出全部牌型待牌；空听及未达到评价起胡番的待牌也保留。" : "以下是摸入后可降低一向听的牌；展开可继续模拟摸打。"
      : "逐种比较出牌。最低向听的方案参与评分，其余方案可另行查看。分数是连续进张价值。");
    if (!r) return;
    if (r.win) {
      const card = element("section", "calc-win");
      card.append(element("h3", "", r.win.qualifies ? `可自摸 · ${r.win.fan} 番` : `牌型已成立 · ${r.win.fan} 番，未达起胡门槛`));
      this.renderWin(card, r.win); root.append(card);
    }
    if (r.hand_size === 13) {
      this.renderImprovements(root, r.improvements, this.snapshot, r.complete);
    } else if (r.candidates.length) {
      const rows = [...r.candidates].sort((a,b) => (a.shanten ?? 99) - (b.shanten ?? 99) || (b.value ?? -1) - (a.value ?? -1));
      const completed = rows.filter(row => row.eligible && row.complete);
      const best = r.complete && completed.length ? Math.max(...completed.map(row => row.value)) : null;
      for (const row of rows) {
        const details = element("details", "calc-candidate"); details.dataset.discard = row.discard;
        const heading = element("summary", "calc-candidate-heading");
        heading.append(this.makeTile(row.discard), element("strong", "", `打 ${row.discard}`),
          element("span", "", this.shantenText(row.shanten)), element("strong", "calc-candidate-value", row.eligible === false ? "—" : this.format(row.value)),
          element("small", "", row.eligible === false ? "向听较高，本次不评分" : best !== null && Math.abs(row.value - best) < 1e-12 ? "最高评价" : row.complete ? best === null ? "已计算" : `差 ${this.format(best - row.value)}` : "待继续分析"));
        details.append(heading);
        const body = element("div", "calc-candidate-body");
        const button = element("button", "secondary-button", "采用这张出牌，继续看 13 张分析"); button.onclick = () => this.applyDiscard(row.discard); body.append(button);
        const hand = [...r.hand]; hand.splice(hand.indexOf(row.discard), 1);
        const snapshot = { ...this.snapshot, hand, visible: [...this.snapshot.visible, row.discard] };
        if (row.eligible === false) body.append(element("p", "calc-hint", "此方案的向听数较高，不参与本次评分。采用后可单独分析它的进张。"));
        else this.renderImprovements(body, row.improvements, snapshot, row.complete);
        details.append(body); root.append(details);
      }
    }
  },

  renderImprovements(root, items, snapshot, complete) {
    const heading = element("h3", "calc-section-title");
    const copies = items.reduce((sum, item) => sum + item.remaining_count, 0);
    heading.textContent = `${complete ? "" : "已找到 "}${items.length} 种 · ${copies} 张未见副本`;
    root.append(heading);
    if (!items.length) { root.append(element("p", "calc-hint", complete ? "没有可用的推进路线。" : "这一部分尚未完成，可继续分析。")); return; }
    const strip = element("div", "calc-improvement-strip"); root.append(strip);
    for (const item of items) {
      const details = element("details", "calc-improvement"); details.dataset.incoming = item.tile;
      const summary = element("summary", "calc-improvement-heading");
      const description = item.empty ? "空听 · 0 张可用" : `${item.remaining_count} 张未见`;
      summary.append(this.makeTile(item.tile), element("strong", "", `摸 ${item.tile}`), element("span", item.empty ? "calc-empty" : "", description),
        element("small", "", item.win ? `${item.win.fan} 番${item.win.qualifies ? "" : " · 未达门槛"}` : `进入 ${this.shantenText(item.next_shanten)}`),
        element("span", "calc-contribution", `贡献 ${this.format(item.contribution)}`));
      details.append(summary);
      const body = element("div", "calc-improvement-body");
      const simulate = element("button", "secondary-button", "模拟摸入这张牌，比较出牌");
      simulate.disabled = item.remaining_count === 0 || snapshot.draws_left === 0;
      simulate.onclick = () => this.applyDraw(item.tile, snapshot); body.append(simulate);
      if (item.win) this.renderWin(body, item.win);
      else body.append(element("p", "calc-hint", `摸入后的最优评价：${this.format(item.value)}。贡献为未见张数占比乘以该评价。`));
      details.append(body);
      if (item.win) details.ontoggle = async () => {
        if (!details.open || details.dataset.loading === "true" || details.dataset.loaded === "true") return;
        details.dataset.loading = "true";
        let area = body.querySelector(".calc-all-solutions");
        if (!area) { area = element("div", "calc-all-solutions"); body.append(area); }
        area.textContent = "正在读取全部分组…";
        try {
          const answer = await this.request({ ...snapshot, action: "explain", tile: item.tile });
          area.replaceChildren();
          answer.solutions.forEach((solution, index) => {
            const section = element("section", "practice-solution"); section.append(element("h3", "", `${solution.family} · 方案 ${index + 1}`));
            this.renderGroups(section, solution.groups); area.append(section);
          });
          details.dataset.loaded = "true";
        } catch (error) { area.textContent = `${error.message}；收起后展开可重试。`; }
        finally { details.dataset.loading = "false"; }
      };
      root.append(details);
      const button = this.makeTile(item.tile, true); button.title = `查看 ${item.tile} 的进张分析`;
      button.onclick = () => { details.open = true; details.scrollIntoView({ block: "nearest" }); };
      if (item.empty) button.classList.add("calc-empty-tile"); strip.append(button);
    }
  },
  renderWin(root, win) {
    root.append(element("p", "calc-hint", `${win.family} · ${win.fan} 番 · ${win.qualifies ? `胡牌评价 ${this.format(win.value)}` : "未达评价起胡门槛"}`));
    const fans = element("p", "calc-fans", win.fans.map(item => `${item.name} ${item.fan}番`).join("、")); root.append(fans);
    this.renderGroups(root, win.groups);
  },
  renderGroups(root, groups) {
    const row = element("div", "practice-groups");
    for (const group of groups) {
      const block = element("div", "practice-group"), cards = element("div", "practice-group-tiles");
      for (const face of group.tiles) { const node = this.makeTile(face.value); if (face.winning) node.classList.add("practice-winning"); cards.append(node); }
      block.append(cards, element("span", "practice-group-label", group.label)); row.append(block);
    }
    root.append(row);
  },
  async applyDraw(value, snapshot = this.snapshot) {
    if (this.busy || !snapshot || snapshot.hand.length !== 13) return;
    if (this.tiles.length !== 13) this.tiles = snapshot.hand.map(face => ({ id: ++this.sequence, value: face }));
    this.visible = new Map(VALUES.map(face => [face, snapshot.visible.filter(v => v === face).length]));
    if (this.remaining(value) <= 0) return;
    $("calc-min-fan").value = snapshot.min_fan;
    $("calc-limit-draws").checked = snapshot.draws_left !== null;
    if (snapshot.draws_left !== null) $("calc-draws").value = snapshot.draws_left;
    const record = { id: ++this.sequence, value }; this.tiles.push(record); this.drawnId = record.id;
    if ($("calc-limit-draws").checked) $("calc-draws").value = Math.max(0, Number($("calc-draws").value) - 1);
    this.changed(); await this.analyze();
  },
  async applyDiscard(value) {
    if (this.busy || this.tiles.length !== 14) return;
    const index = this.tiles.findIndex(item => item.value === value); if (index < 0) return;
    this.tiles.splice(index, 1); this.drawnId = null; this.visible.set(value, (this.visible.get(value) || 0) + 1);
    this.changed(); await this.analyze();
  },
};
