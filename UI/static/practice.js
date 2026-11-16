"use strict";

window.listeningPractice = {
  active: false,
  question: null,
  order: [],
  nodes: new Map(),
  choices: new Map(),
  selected: new Set(),
  notReady: false,
  loading: false,
  starting: false,
  submitting: false,
  revision: 0,
  questionNumber: 0,
  receivedAt: 0,
  resultsKey: null,
  drag: null,
  touch: null,
  locallyExpired: false,
  expiryPending: false,
  nextRefresh: 0,

  initialize() {
    $("practice-seconds").required = true;
    $("practice-timed").onchange = () => this.renderControls();
    $("practice-new").onclick = () => this.newQuestion();
    $("practice-submit").onclick = () => this.finish("submit");
    $("practice-reveal").onclick = () => this.finish("reveal");
    $("practice-not-ready").onclick = () => {
      if (!this.canAnswer()) return;
      this.notReady = !this.notReady;
      this.selected.clear();
      this.renderChoices(); this.renderControls(); this.saveDraft();
    };
    $("practice-sort").onclick = () => {
      this.order.sort((left, right) => VALUES.indexOf(left.value) - VALUES.indexOf(right.value));
      this.renderHand();
    };
    const hand = $("practice-hand");
    hand.ondragover = (event) => {
      if (!this.drag) return;
      event.preventDefault(); this.previewDrop(event.clientX, event.clientY);
    };
    hand.ondrop = (event) => {
      if (!this.drag) return;
      event.preventDefault(); this.commitDrop(event.clientX, event.clientY);
    };
    window.addEventListener("resize", () => { if (this.drag) this.drag.geometry = null; });
    this.renderHand(); this.renderControls();
    setInterval(() => this.renderTimer(), 200);
  },

  enter() {
    this.active = true;
    if (!this.choices.size) {
      for (const value of VALUES) {
        const node = this.makeTile(value, true);
        node.setAttribute("aria-pressed", "false");
        node.onclick = () => {
          if (!this.canAnswer()) return;
          this.notReady = false;
          if (this.selected.has(value)) this.selected.delete(value);
          else this.selected.add(value);
          this.renderChoices(); this.renderControls(); this.saveDraft();
        };
        this.choices.set(value, node); $("practice-palette").append(node);
      }
    }
    this.render();
    if (this.question?.phase === "READY") this.start();
    else if (this.question?.phase === "ANSWERING") this.refresh();
  },

  leave() { this.active = false; this.clearDrag(); },
  finished() { return this.question && ["CORRECT", "INCORRECT", "TIMED_OUT", "REVEALED"].includes(this.question.phase); },
  now() { return (this.question?.server_now || 0) + (performance.now() - this.receivedAt) / 1000; },
  canAnswer() {
    return this.question?.phase === "ANSWERING" && !this.submitting && !this.locallyExpired
      && (this.question.deadline === null || this.now() < this.question.deadline);
  },
  makeTile(value, interactive = false) {
    const node = tile(value, interactive);
    node.classList.remove("number-view");
    return node;
  },
  error(message = "") {
    $("practice-error").textContent = message;
    $("practice-error").hidden = !message;
  },

  async request(action, data = {}) {
    const response = await fetch("/api/practice", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...data }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "练习请求失败，请重试");
    return result.question;
  },

  async newQuestion() {
    if (this.loading || this.starting || this.question?.phase === "ANSWERING") return;
    if ($("practice-timed").checked && !$("practice-seconds").reportValidity()) return;
    this.loading = true; this.error(); this.renderControls(); this.renderResults();
    try {
      const question = await this.request("new", {
        seconds: $("practice-timed").checked ? Number($("practice-seconds").value) : null,
      });
      this.clearDrag();
      this.question = question; this.order = [...question.hand];
      this.nodes.clear(); $("practice-hand").replaceChildren();
      this.selected = new Set(); this.notReady = false; this.revision = 0;
      this.questionNumber++; this.locallyExpired = false; this.resultsKey = null;
      this.receivedAt = performance.now();
      this.loading = false; this.render();
      if (this.active) await this.start();
    } catch (error) {
      this.error(error.message || "题目生成失败，请重试");
    } finally { this.loading = false; this.renderControls(); this.renderResults(); }
  },

  async start() {
    if (this.starting || this.question?.phase !== "READY") return;
    this.starting = true;
    const id = this.question.question_id;
    this.renderControls();
    try {
      await Promise.all([...$("practice-hand").querySelectorAll("img")].map(img => img.decode().catch(() => {})));
      await new Promise(resolve => requestAnimationFrame(resolve));
      if (!this.active || this.question.question_id !== id) return;
      this.accept(await this.request("start", { question_id: id }));
    } catch (error) { this.error(error.message || "本题启动失败，请重试"); }
    finally { this.starting = false; this.renderControls(); }
  },

  accept(question) {
    if (question.question_id !== this.question?.question_id) return;
    if (this.finished() && question.phase === "ANSWERING") return;
    if (question.answer_revision < this.question.answer_revision) return;
    this.question = question; this.receivedAt = performance.now();
    if (this.finished()) {
      this.selected = new Set(question.selected); this.notReady = question.not_ready;
    }
    this.render();
  },

  async saveDraft() {
    if (!this.canAnswer()) return;
    const id = this.question.question_id;
    const data = { question_id: id, tiles: [...this.selected], not_ready: this.notReady, revision: ++this.revision };
    try { this.accept(await this.request("draft", data)); }
    catch (error) { if (id === this.question?.question_id && !this.finished()) this.error(error.message || "选择保存失败，请重试"); }
  },

  async finish(action) {
    if (!this.canAnswer()) return;
    if (action === "submit" && !this.selected.size && !this.notReady) return;
    this.submitting = true; this.error(); this.renderControls(); this.renderChoices();
    const id = this.question.question_id;
    try {
      this.accept(await this.request(action, { question_id: id, tiles: [...this.selected],
        not_ready: this.notReady, revision: ++this.revision }));
    } catch (error) { this.error(error.message || "答案提交失败，请重试"); }
    finally { this.submitting = false; this.renderControls(); this.renderChoices(); }
  },

  async refresh() {
    if (!this.question || this.expiryPending || performance.now() < this.nextRefresh) return;
    const id = this.question.question_id;
    this.expiryPending = true;
    try {
      const response = await fetch(`/api/practice/${encodeURIComponent(id)}`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "练习状态读取失败");
      this.accept(result.question);
    } catch (error) {
      if (id === this.question?.question_id) this.error(error.message);
      this.nextRefresh = performance.now() + 1000;
    } finally { this.expiryPending = false; }
  },

  render() {
    this.renderHand(); this.renderControls(); this.renderChoices(); this.renderResults(); this.renderTimer();
  },

  renderControls() {
    const question = this.question;
    const locked = this.loading || this.starting || question?.phase === "ANSWERING";
    $("practice-timed").disabled = locked;
    $("practice-seconds").disabled = locked || !$("practice-timed").checked;
    $("practice-settings-hint").textContent = locked ? "本题设置已锁定；限时离开页面后仍继续" : "取消勾选即可不限时";
    $("practice-new").disabled = locked;
    $("practice-new").textContent = this.loading ? "正在生成题目…" : question ? "下一题" : "开始练习";
    $("practice-submit").disabled = !this.canAnswer() || !this.selected.size && !this.notReady;
    $("practice-reveal").disabled = !this.canAnswer();
    $("practice-not-ready").disabled = !this.canAnswer();
    $("practice-sort").disabled = !question || this.loading || question.phase === "READY";
    $("practice-question-number").textContent = question ? `第 ${this.questionNumber} 题` : "";
    $("practice-status").textContent = this.loading ? "正在生成题目…" : this.starting ? "正在准备手牌…"
      : !question ? "准备开始" : { READY: "题目已准备好", ANSWERING: "作答中", CORRECT: "回答正确",
        INCORRECT: "已交卷", TIMED_OUT: "时间到", REVEALED: "已查看答案" }[question.phase];
    $("practice-selection-count").textContent = this.notReady ? "已选：不能听牌" : this.selected.size ? `已选 ${this.selected.size} 种` : "可选择多种牌";
  },

  renderTimer() {
    const question = this.question, root = $("practice-timer");
    root.hidden = !question || question.phase === "READY";
    if (!question) return;
    if (this.finished()) {
      const text = question.phase === "TIMED_OUT" ? "时间到" : "已结束";
      if (root.textContent !== text) root.textContent = text;
      root.classList.toggle("urgent", question.phase === "TIMED_OUT");
      return;
    }
    if (question.phase !== "ANSWERING") return;
    if (question.deadline === null) {
      if (root.textContent !== "不限时") root.textContent = "不限时";
      root.classList.remove("urgent"); return;
    }
    const seconds = Math.max(0, question.deadline - this.now());
    const text = `${Math.ceil(seconds)} 秒`;
    if (root.textContent !== text) root.textContent = text;
    root.classList.toggle("urgent", seconds <= 5);
    if (seconds <= 0) {
      if (!this.locallyExpired) { this.locallyExpired = true; this.renderControls(); this.renderChoices(); }
      this.refresh();
    }
  },

  renderChoices() {
    const done = this.finished(), waits = new Set(this.question?.waits || []);
    for (const [value, node] of this.choices) {
      node.disabled = !this.canAnswer();
      node.setAttribute("aria-pressed", String(this.selected.has(value)));
      node.classList.toggle("practice-picked", !done && this.selected.has(value));
      node.classList.toggle("practice-correct", done && waits.has(value) && this.selected.has(value));
      node.classList.toggle("practice-missed", done && waits.has(value) && !this.selected.has(value));
      node.classList.toggle("practice-extra", done && !waits.has(value) && this.selected.has(value));
    }
    const none = $("practice-not-ready");
    none.setAttribute("aria-pressed", String(this.notReady));
    none.classList.toggle("practice-picked", !done && this.notReady);
    none.classList.toggle("practice-correct", done && !waits.size && this.notReady);
    none.classList.toggle("practice-missed", done && !waits.size && !this.notReady);
    none.classList.toggle("practice-extra", done && !!waits.size && this.notReady);
    $("practice-choice-legend").hidden = !done;
  },

  renderHand() {
    if (this.drag) return;
    const root = $("practice-hand");
    if (!this.question) {
      if (!root.childElementCount) for (let i = 0; i < 13; i++) root.append(element("span", "preview-card-slot", i + 1));
      return;
    }
    this.order.forEach(({ tile_id: id, value }, index) => {
      let node = this.nodes.get(id);
      if (!node) {
        node = this.makeTile(value, true); node.dataset.practiceTileId = id;
        node.title = `${value} 牌 · 拖动排序，或按 Alt + 左右方向键移动`;
        node.ondragstart = (event) => {
          if (node.disabled) { event.preventDefault(); return; }
          this.drag = { id, questionId: this.question.question_id, geometry: null };
          event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", id);
          node.classList.add("dragging");
        };
        node.ondragend = () => this.clearDrag();
        node.onkeydown = (event) => {
          if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
          event.preventDefault();
          const position = this.order.findIndex(item => item.tile_id === id);
          this.moveTile(id, Math.max(0, Math.min(this.order.length - 1, position + (event.key === "ArrowLeft" ? -1 : 1))));
          node.focus();
        };
        node.onpointerdown = (event) => {
          if (event.pointerType === "mouse" || node.disabled) return;
          this.touch = { id, pointerId: event.pointerId, x: event.clientX, y: event.clientY };
          node.setPointerCapture(event.pointerId);
        };
        node.onpointermove = (event) => {
          if (this.touch?.id !== id || this.touch.pointerId !== event.pointerId) return;
          if (!this.drag && Math.hypot(event.clientX - this.touch.x, event.clientY - this.touch.y) < 7) return;
          event.preventDefault();
          if (!this.drag) { this.drag = { id, questionId: this.question.question_id, geometry: null }; node.classList.add("dragging"); }
          this.previewDrop(event.clientX, event.clientY);
        };
        node.onpointerup = (event) => { if (this.touch?.id === id) { if (this.drag) this.commitDrop(event.clientX, event.clientY); else this.touch = null; } };
        node.onpointercancel = (event) => {
          // Native mouse dragging cancels the pointer stream as dragstart
          // takes over; only cancel a touch gesture that we own here.
          if (this.touch?.pointerId === event.pointerId) this.clearDrag();
        };
        this.nodes.set(id, node);
      }
      const disabled = this.loading || this.question.phase === "READY";
      if (node.disabled !== disabled) node.disabled = disabled;
      if (node.draggable !== !disabled) node.draggable = !disabled;
      if (root.children[index] !== node) root.insertBefore(node, root.children[index] || null);
    });
  },

  dropTarget(x, y, fresh = false) {
    if (!this.drag) return null;
    if (fresh) this.drag.geometry = null;
    if (!this.drag.geometry) {
      const bounds = $("practice-hand").getBoundingClientRect();
      const targets = [...$("practice-hand").querySelectorAll("[data-practice-tile-id]")]
        .filter(node => node.dataset.practiceTileId !== this.drag.id).map(node => ({ node, rect: node.getBoundingClientRect() }));
      this.drag.geometry = { bounds, targets };
    }
    const { bounds, targets } = this.drag.geometry;
    let index = targets.findIndex(({ rect }) => y < rect.top || y <= rect.bottom && x < (rect.left + rect.right) / 2);
    if (index < 0) index = targets.length;
    const next = targets[index], last = targets.at(-1);
    const rect = next?.rect || last?.rect;
    return { index, left: (next ? rect.left - 4 : rect.right + 3) - bounds.left,
      top: rect.top - bounds.top, height: rect.height };
  },

  previewDrop(x, y) {
    const target = this.dropTarget(x, y);
    if (!target) return;
    const root = $("practice-hand");
    let marker = root.querySelector(".drop-slot");
    if (!marker) { marker = element("span", "drop-slot"); root.append(marker); }
    const css = `left:${target.left}px;top:${target.top}px;height:${target.height}px;bottom:auto`;
    if (this.drag.markerKey !== css) { marker.style.cssText = css; this.drag.markerKey = css; }
  },

  commitDrop(x, y) {
    if (!this.drag) return;
    const { id, questionId } = this.drag;
    const target = this.dropTarget(x, y, true);
    this.clearDrag(false);
    if (target && questionId === this.question?.question_id) this.moveTile(id, target.index);
    else this.renderHand();
  },

  moveTile(id, index) {
    const from = this.order.findIndex(item => item.tile_id === id);
    if (from < 0) return;
    const [record] = this.order.splice(from, 1); this.order.splice(index, 0, record);
    this.renderHand();
  },

  clearDrag(refresh = true) {
    this.drag = null; this.touch = null;
    $("practice-hand").querySelector(".drop-slot")?.remove();
    for (const node of this.nodes.values()) node.classList.remove("dragging");
    if (refresh && this.question) this.renderHand();
  },

  renderResults() {
    const done = this.finished();
    $("practice-results").hidden = !done || this.loading;
    if (!done) return;
    const q = this.question, key = `${q.question_id}:${q.phase}:${q.answer_revision}`;
    if (this.resultsKey === key) return;
    this.resultsKey = key;
    $("practice-result-kicker").textContent = "答案与分组解答";
    $("practice-result-title").textContent = { CORRECT: "答对了", INCORRECT: "再看一看这副牌", TIMED_OUT: "时间到", REVEALED: "本题答案" }[q.phase];
    $("practice-result-summary").textContent = q.waits.length ? `共听 ${q.waits.length} 种牌` : "这副牌不能听";
    $("practice-result-message").textContent = !q.waits.length ? "补入任意合法牌种，都无法形成完整胡牌牌型。"
      : q.phase === "CORRECT" ? "所有待牌都选对了。展开每张待牌，可以查看如何组成完整牌型。"
      : `漏选 ${q.missed.length} 种，误选 ${q.extra.length} 种。下面列出了全部正确待牌与分组。`;
    $("practice-solution-hint").hidden = !q.waits.length;
    const summary = $("practice-correct-tiles"), solutions = $("practice-solutions");
    summary.replaceChildren(); solutions.replaceChildren();
    q.answers.forEach((answer, index) => {
      const details = element("details", "practice-answer-card");
      const heading = element("summary", "practice-answer-heading");
      heading.append(this.makeTile(answer.tile), element("strong", "", `补入 ${answer.tile}`),
        element("span", answer.empty ? "practice-empty-wait" : "", answer.empty ? "空听 · 副本已全部在手中" : `剩余 ${answer.remaining_count} 张`),
        element("small", "", `${answer.solutions.length} 种分组方案`));
      details.append(heading);
      details.ontoggle = () => {
        if (!details.open || details.dataset.rendered) return;
        details.dataset.rendered = "true";
        answer.solutions.forEach((solution, solutionIndex) => {
          const section = element("section", "practice-solution");
          section.append(element("h3", "", `${solution.family} · 方案 ${solutionIndex + 1}`));
          const groups = element("div", "practice-groups");
          solution.groups.forEach((group) => {
            const block = element("div", "practice-group"), cards = element("div", "practice-group-tiles");
            group.tiles.forEach((face) => {
              const node = this.makeTile(face.value);
              if (face.winning) {
                node.classList.add("practice-winning");
                node.title = `补入的 ${face.value} 牌`;
                node.setAttribute("aria-label", node.title);
              }
              cards.append(node);
            });
            block.append(cards, element("span", "practice-group-label", group.label)); groups.append(block);
          });
          section.append(groups); details.append(section);
        });
      };
      solutions.append(details);
      const button = this.makeTile(answer.tile, true);
      button.setAttribute("aria-label", `查看 ${answer.tile} 牌的全部分组解答`);
      button.onclick = () => { details.open = true; details.scrollIntoView({ block: "nearest" }); heading.focus(); };
      summary.append(button);
      if (index === 0) details.open = true;
    });
  },
};
