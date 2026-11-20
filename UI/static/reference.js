"use strict";

// The reference loads once on demand. It does not advance or poll a game.
window.ruleReference = {
  data: null, loading: null, selected: "rules", items: { rules: [], pool: [], fans: [] }, previousFocus: null,
  initialize() {
    $("reference-toggle").onclick = () => this.open();
    $("reference-close").onclick = () => $("reference-dialog").close();
    $("reference-search").oninput = () => this.filter();
    $("reference-dialog").addEventListener("close", () => {
      $("reference-toggle").setAttribute("aria-expanded", "false");
      const focus = this.previousFocus?.getClientRects().length ? this.previousFocus : $("reference-toggle"); focus.focus();
    });
    const tabs = ["rules", "pool", "fans"];
    tabs.forEach((name, index) => {
      const tab = $("reference-tab-" + name);
      tab.onclick = () => this.select(name);
      tab.onkeydown = event => {
        const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault(); this.select(tabs[next]); $("reference-tab-" + tabs[next]).focus();
      };
    });
  },
  async open(tab = this.selected, query = "") {
    if (!$("reference-dialog").open) { this.previousFocus = document.activeElement; $("reference-dialog").showModal(); }
    $("reference-toggle").setAttribute("aria-expanded", "true"); this.select(tab, query);
    if (!this.data) {
      if (!this.loading) this.loading = this.load().finally(() => { this.loading = null; });
      await this.loading;
    }
    this.filter();
    if ($("reference-dialog").open) $("reference-search").focus();
  },
  select(name, query = "") {
    this.selected = ["rules", "pool", "fans"].includes(name) ? name : "rules";
    for (const tab of ["rules", "pool", "fans"]) {
      const active = tab === this.selected, button = $("reference-tab-" + tab);
      button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
      $("reference-" + tab).hidden = !active;
    }
    $("reference-search").value = query; this.filter();
  },
  async load() {
    $("reference-status").textContent = "正在读取本机规则…";
    try {
      const response = await fetch("/api/reference"), data = await response.json();
      if (!response.ok) throw new Error(data.error || "规则读取失败");
      this.data = data; this.build();
    } catch (error) { $("reference-status").textContent = `${error.message}，关闭后重新打开可重试。`; }
  },
  remember(tab, node, search) { this.items[tab].push({ node, search: String(search).toLowerCase() }); },
  build() {
    const data = this.data;
    for (const rule of data.rules) {
      const article = element("article", "reference-rule"), title = element("h3", "", rule.title); article.append(title);
      rule.paragraphs.forEach(text => article.append(element("p", "", text))); $("reference-rules").append(article);
      this.remember("rules", article, [rule.title, ...rule.paragraphs].join(" "));
    }
    $("reference-pool").append(element("p", "reference-intro", `共 ${data.tiles.length} 种、${data.total} 张：数字牌 ${data.number_total} 张，符号牌 ${data.symbol_total} 张。`));
    const table = element("table", "reference-pool-table"), head = element("thead"), tr = element("tr");
    for (const label of ["牌值", "传统图案", "固定张数"]) { const th = element("th", "", label); th.scope = "col"; tr.append(th); }
    head.append(tr); table.append(head); const body = element("tbody"); table.append(body); $("reference-pool").append(table);
    for (const item of data.tiles) {
      const row = element("tr", "reference-pool-row"); row.dataset.value = item.value;
      const face = element("th"); face.scope = "row"; face.append(handCalculator.makeTile(item.value), element("span", "", item.value));
      row.append(face, element("td", "", item.traditional), element("td", "", `${item.count} 张`)); body.append(row);
      this.remember("pool", row, `${item.value} ${item.traditional} ${item.count}张`);
    }
    $("reference-fans").append(element("p", "reference-intro", `1 番起胡，多一番翻倍，实际结算最多 ${data.max_fan} 番。涉及数字范围或性质的番种至少需要一张数字牌；符号牌不参与这些数字条件判断。`));
    for (const fan of data.fans) {
      const article = element("article", "reference-fan"); article.dataset.fan = fan.code; article.dataset.points = fan.fan;
      const header = element("div", "reference-fan-heading"); header.append(element("h3", "", fan.name), element("strong", "", `${fan.fan} 番`));
      article.append(header, element("p", "", fan.description));
      if (fan.excludes.length) article.append(element("p", "reference-exclusion", `不重复计入：${fan.excludes.join("、")}`));
      $("reference-fans").append(article);
      this.remember("fans", article, `${fan.name} ${fan.code} ${fan.fan}番 ${fan.description} ${fan.excludes.join(" ")}`);
    }
  },
  filter() {
    if (!this.data) return;
    const query = $("reference-search").value.trim().toLowerCase(), words = query.split(/\s+/).filter(Boolean);
    let count = 0;
    for (const item of this.items[this.selected]) {
      item.node.hidden = !words.every(word => item.search.includes(word)); if (!item.node.hidden) count++;
    }
    $("reference-status").textContent = count ? `${{ rules: "规则", pool: "牌池构成", fans: "番数表" }[this.selected]} · ${count} 项${query ? "匹配" : ""}` : "没有匹配内容，试试其他关键词。";
  },
};
