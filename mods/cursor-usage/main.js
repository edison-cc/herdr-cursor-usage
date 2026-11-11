// Cursor usage: a toolbar button opens one dashboard tab.
import { View, div, svg } from "gpui-kit";
import { h_flex, v_flex } from "gpui-base";
import { appHome, exec, panes, props, readTextFile, run, settings, spaces, theme, writeTextFile } from "herdr";
import { cacheText, cachedReport, claimLock, isFetchInFlight, lockRecord, showCachedThenFetch, FETCH_LIMIT_MS } from "./cache.js";
import { G, T, button, card, statCell, stateCard, tokens } from "./ui.js";

const EVERY = 5 * 60 * 1000;
const PULL = 30 * 1000;
const TAB = { name: "Cursor usage", mod: "cursor-usage" };

function showUsage() {
  const active = spaces().find((space) => space.active);
  const tabIds = new Set(active?.tabs.map((tab) => tab.id) ?? []);
  const open = panes().find((pane) => pane.mod === "cursor-usage" && tabIds.has(pane.tab));
  if (open) {
    const zoomed = panes().find((pane) => pane.tab === open.tab && pane.zoomed);
    if (zoomed && zoomed.pane !== open.pane) run("pane.zoom", { pane: zoomed.pane });
    return run("pane.focus", { pane: open.pane });
  }
  return run("tab.new", TAB);
}

function ago(at, now) {
  const min = Math.round((now - at) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

async function runCollector() {
  const script = `${appHome()}/mods/cursor-usage/cursor_usage.py`;
  const options = { timeout: 90000 };
  try {
    return await exec("python3", [script], options);
  } catch (startError) {
    try {
      // Dock launches often miss the login-shell PATH. The path is an argument, not interpolated.
      return await exec("sh", [
        "-c",
        "exec \"${SHELL:-/bin/sh}\" -lic 'exec python3 \"$1\"' sh \"$1\"",
        "sh",
        script,
      ], options);
    } catch {
      throw startError;
    }
  }
}

function cachePath() {
  return `${appHome()}/data/cursor-usage/report.json`;
}

function lockPath() {
  return `${appHome()}/data/cursor-usage/fetching`;
}

async function loadReport() {
  const result = await runCollector();
  const text = String(result?.stdout || "");
  const start = text.indexOf("{");
  if (!result || result.code !== 0 || start < 0) {
    const line = String(result?.stderr || text || `exit ${result?.code}`).trim().split("\n").pop();
    throw new Error(line || "Cursor usage failed");
  }
  let data;
  try {
    data = JSON.parse(text.slice(start));
  } catch {
    throw new Error("Cursor usage returned an unexpected response");
  }
  if (!data.ok) throw new Error(data.error || "Cursor usage failed");
  return data;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAY = 86400000;
const CELL = 10;
const SERIES = {
  all: { label: "All", title: "AI line edits", noun: "line", plural: "lines" },
  tab: { label: "Tab", title: "Tab completions", noun: "completion", plural: "completions" },
  agent: { label: "Agent", title: "Agent lines of code", noun: "line", plural: "lines" },
};
const SEGMENT_COLOR = {
  "grok-bot": "#e8a23a",
  cursor: "#4c8dff",
  grok: "#e8a23a",
  api: "#8b9bb4",
  other: "#8b9bb4",
};
const HEAT = ["#1a3f66", "#245b96", "#3b82f6", "#8eb6ff"];

function shortDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  if (!match) return "";
  return `${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}`;
}

function countPhrase(value, series) {
  const spec = SERIES[series] || SERIES.all;
  const count = Number(value) || 0;
  if (count === 1) return `1 ${spec.noun}`;
  return `${count.toLocaleString()} ${spec.plural}`;
}

function utcDay(ms) {
  const date = new Date(ms);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

function isoDay(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function yearWeeks(now) {
  const end = utcDay(now);
  const raw = end - 364 * DAY;
  const start = raw - new Date(raw).getUTCDay() * DAY;
  const weeks = [];
  for (let cursor = start; cursor <= end; cursor += 7 * DAY) {
    const days = [];
    for (let offset = 0; offset < 7; offset += 1) {
      const ms = cursor + offset * DAY;
      if (ms <= end) days.push(ms);
    }
    weeks.push(days);
  }
  return weeks;
}

function heatLevel(value, max) {
  if (!value || max <= 0) return 0;
  return Math.min(4, Math.ceil((value / max) * 4));
}

function editStats(cells) {
  const active = cells.filter((cell) => cell.value > 0);
  const total = active.reduce((sum, cell) => sum + cell.value, 0);
  let busiest = null;
  const months = new Map();
  for (const cell of active) {
    if (!busiest || cell.value > busiest.value) busiest = cell;
    const key = cell.date.slice(0, 7);
    months.set(key, (months.get(key) || 0) + cell.value);
  }
  let busiestMonth = "";
  let busiestMonthValue = -1;
  for (const [key, value] of months) {
    if (value > busiestMonthValue) {
      busiestMonth = key;
      busiestMonthValue = value;
    }
  }
  let run = 0;
  let longest = 0;
  let current = 0;
  cells.forEach((cell, index) => {
    if (cell.value > 0) {
      run += 1;
      longest = Math.max(longest, run);
      if (index === cells.length - 1) current = run;
      return;
    }
    run = 0;
  });
  return { total, busiest, busiestMonth, longest, current };
}

function dayPhrase(count) {
  return count === 1 ? "1 day" : `${count} days`;
}

function compactTokens(count) {
  const value = Number(count) || 0;
  if (value >= 1000000000) return `${(value / 1000000000).toFixed(1)}B`;
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 10000) return `${(value / 1000).toFixed(1)}K`;
  return value.toLocaleString();
}

function meterBar(percent, elapsed, blocked, width, c) {
  const pct = Math.min(100, Math.max(0, Number(percent) || 0));
  const track = width || 0;
  const fill = Math.round(track * pct / 100);
  const hasTick = Number.isFinite(elapsed);
  const tick = hasTick ? Math.round(track * Math.min(1, Math.max(0, elapsed))) : 0;
  return div().id("cursor-usage-bar").relative().w_full().h(8).rounded(4).bg(c.border)
    .child(div().absolute().left(0).top(0).h(8).rounded(4)
      .w(Math.max(pct > 0 ? 8 : 0, fill))
      .bg(blocked ? c.destructive : c.text))
    .when(hasTick, (el) => el.child(div().absolute().top(-2).h(12).w(1).left(tick).bg(c.text).opacity(0.45)));
}

export default class CursorUsage extends View {
  init(_props, cx) {
    this.toolbarMode = props()?.mode === "toolbar";
    this.report = null;
    this.error = null;
    this.loading = false;
    this.updatedAt = null;
    this.width = 0;
    this.barWidth = 0;
    this.series = "all";
    this.cacheText = null;
    if (this.toolbarMode) {
      // The button stays on the layout after the usage tab is closed, so this
      // is the clock that keeps the saved report at most five minutes old.
      cx.spawn((taskCx) => this.warm(taskCx));
      cx.timer.every(EVERY, (timerCx) => {
        timerCx.spawn((taskCx) => this.warm(taskCx));
      });
      return;
    }
    // The check renders this frame and does not wait. Keep the loading card
    // until a cache or a live report arrives.
    this.loading = true;
    cx.spawn(async (taskCx) => {
      await this.restore();
      const files = await this.readFetchFiles();
      if (isFetchInFlight(files.cacheText, files.lockText, Date.now())) {
        const startedAt = lockRecord(files.lockText)?.startedAt ?? null;
        if (this.report) taskCx.notify();
        await this.waitForCache(taskCx);
        const arrived = this.updatedAt != null && startedAt != null && this.updatedAt >= startedAt;
        if (arrived) {
          this.loading = false;
          taskCx.notify();
          return;
        }
      }
      this.loading = false;
      if (this.report) {
        taskCx.notify();
        return;
      }
      this.refresh(taskCx);
    });
    cx.timer.every(PULL, (timerCx) => {
      timerCx.spawn((taskCx) => this.pull(taskCx));
    });
  }

  async pull(cx) {
    if (this.loading) return;
    let text = null;
    try {
      text = await readTextFile(cachePath());
    } catch {
      return;
    }
    const cached = cachedReport(text);
    if (!cached || cached.savedAt === this.updatedAt) return;
    if (this.updatedAt != null && cached.savedAt < this.updatedAt) return;
    this.cacheText = text;
    this.report = cached.report;
    this.updatedAt = cached.savedAt;
    this.error = null;
    cx.notify();
  }

  async readFetchFiles() {
    let cacheText = null;
    let lockText = null;
    try {
      cacheText = await readTextFile(cachePath());
    } catch {
      cacheText = null;
    }
    try {
      lockText = await readTextFile(lockPath());
    } catch {
      lockText = null;
    }
    return { cacheText, lockText };
  }

  async warm(cx) {
    const files = await this.readFetchFiles();
    if (isFetchInFlight(files.cacheText, files.lockText, Date.now())) return;
    this.cacheText = files.cacheText;
    await this.collect(cx, false);
  }

  async waitForCache(cx) {
    const deadline = Date.now() + FETCH_LIMIT_MS;
    while (Date.now() < deadline) {
      const files = await this.readFetchFiles();
      if (!isFetchInFlight(files.cacheText, files.lockText, Date.now())) break;
      await cx.sleep(400);
    }
    await this.restore();
  }

  async releaseLock(expected) {
    try {
      await writeTextFile(lockPath(), "", expected);
    } catch {
      // A leftover lock expires. The next open can take it.
    }
  }

  async collect(cx, paint) {
    const files = await this.readFetchFiles();
    const claim = claimLock(files.lockText, files.cacheText, Date.now());
    if (!claim) {
      if (!paint) return;
      await this.waitForCache(cx);
      this.loading = false;
      cx.notify();
      return;
    }
    let held = null;
    try {
      await writeTextFile(lockPath(), claim.text, claim.expected);
      held = claim.text;
    } catch {
      if (!paint) return;
      await this.waitForCache(cx);
      this.loading = false;
      cx.notify();
      return;
    }
    try {
      if (this.cacheText == null) this.cacheText = files.cacheText;
      const report = await loadReport();
      this.report = report;
      this.error = null;
      this.updatedAt = Date.now();
      await this.store();
    } catch (error) {
      if (paint) this.error = String(error?.message || error);
    } finally {
      if (held) await this.releaseLock(held);
      if (paint) {
        this.loading = false;
        cx.notify();
      }
    }
  }

  async restore() {
    let text = null;
    try {
      text = await readTextFile(cachePath());
    } catch {
      return;
    }
    showCachedThenFetch(text, (cached) => {
      this.cacheText = text;
      this.report = cached.report;
      this.updatedAt = cached.savedAt;
      this.error = null;
    }, () => null);
  }

  async store() {
    const next = cacheText(this.report, this.updatedAt);
    if (next.includes("WorkosCursorSessionToken") || next.includes("cursor-safe-storage:")) return;
    const path = cachePath();
    try {
      await writeTextFile(path, next, this.cacheText);
      this.cacheText = next;
    } catch {
      try {
        const current = await readTextFile(path);
        await writeTextFile(path, next, current);
        this.cacheText = next;
      } catch {
        // The cards are already showing. A missed write only slows the next open.
      }
    }
  }

  refresh(cx) {
    if (this.loading) return;
    this.loading = true;
    cx.notify();
    cx.spawn((taskCx) => this.collect(taskCx, true));
  }

  toolbar() {
    const colors = theme();
    return h_flex().size_full().pr(10).items_center()
      .bg(colors.chrome).border_b(1).border_color(colors.border)
      .child(h_flex().id("cursor-usage-open").h(26).px(8).gap(5).flex_none()
        .items_center().rounded(6).text_size(12).bg(colors.chrome).text_color(colors.muted)
        .hover((style) => style.bg(colors.hover).text_color(colors.foreground))
        .on_mouse_down("left", (_event, cx) => cx.stop_propagation())
        .on_click(() => showUsage())
        .child(svg("mods/cursor-usage/cursor.svg").size(15))
        .child("Usage"));
  }

  header(c) {
    const status = this.loading
      ? "Refreshing…"
      : this.error && this.report
        ? this.error
        : this.updatedAt
          ? `Updated ${ago(this.updatedAt, Date.now())}`
          : "";
    return h_flex().w_full().gap(G.sm).items_center().justify_end()
      .when(Boolean(status), (el) => el.child(div().text_size(T.small)
        .text_color(this.error ? c.destructive : c.dim).child(status)))
      .child(button("cursor-usage-refresh", "Refresh", (_event, clickCx) => this.refresh(clickCx), c, {
        disabled: this.loading,
      }));
  }

  splitBar(included, c) {
    const segments = included.segments || [];
    const shown = Number(included.percent) || 0;
    const track = this.barWidth || 0;
    let cursor = 0;
    const fills = segments.length
      ? segments.map((segment) => {
        const width = Math.round(track * (Number(segment.percent) || 0) / 100);
        const node = div().absolute().top(0).h(6).left(cursor).w(Math.max(width, segment.percent > 0 ? 2 : 0))
          .bg(SEGMENT_COLOR[segment.product] || c.text);
        cursor += width;
        return node;
      })
      : [div().absolute().left(0).top(0).h(6).rounded(3).w(Math.max(Math.round(track * shown / 100), shown > 0 ? 6 : 0)).bg(c.text)];
    return div().id("cursor-usage-bar").relative().w_full().h(6).rounded(3).overflow_hidden().bg(c.border)
      .on_size_change((size, sizeCx) => {
        if (Math.abs((this.barWidth || 0) - size.width) < 1) return;
        this.barWidth = size.width;
        sizeCx.notify();
      })
      .children(fills);
  }

  includedCard(c) {
    const included = this.report.included;
    const segments = included.segments || [];
    return card(c).flex_1().min_w(280)
      .child(div().text_size(T.small).text_color(c.dim).child(included.plan || "Included usage"))
      .child(h_flex().items_end().gap(G.sm)
        .child(div().text_size(T.metric).font_semibold().text_color(included.paceHot ? c.destructive : c.text)
          .child(`${included.percent}%`))
        .child(div().pb(6).text_size(T.body).text_color(c.mutedForeground).child("used")))
      .child(this.splitBar(included, c))
      .when(segments.length > 0, (el) => el.child(h_flex().w_full().gap(G.md).flex_wrap().children(
        segments.map((segment) => h_flex().gap(6).items_center()
          .child(div().size(8).flex_none().rounded_full().bg(SEGMENT_COLOR[segment.product] || c.text))
          .child(div().text_size(T.small).text_color(c.mutedForeground)
            .child(`${segment.label} ${segment.percent}%`))),
      )))
      .child(h_flex().w_full().justify_between().gap(G.md)
        .child(div().text_size(T.small).text_color(c.dim).child(included.period || "Included usage"))
        .when(Boolean(included.resets), (el) => el.child(div().text_size(T.small).text_color(c.dim).child(included.resets))))
      .when(Boolean(included.pace), (el) => el.child(div().text_size(T.small)
        .text_color(included.paceHot ? c.destructive : c.text).child(included.pace)));
  }

  balanceCard(c) {
    const balance = this.report.balance;
    if (!balance) return null;
    return card(c).flex_1().min_w(280)
      .child(div().text_size(T.small).text_color(c.dim).child("Account balance"))
      .child(h_flex().items_end().gap(G.sm)
        .child(div().text_size(T.metric).font_semibold().text_color(c.text).child(balance.used))
        .child(div().pb(6).text_size(T.body).text_color(c.mutedForeground).child("used this month")))
      .child(h_flex().w_full().justify_between().gap(G.md)
        .child(div().text_size(T.small).text_color(c.dim).child("Monthly additional usage"))
        .child(div().text_size(T.small).text_color(c.text).child(balance.limit || "Not set")))
      .when(Boolean(balance.resets), (el) => el.child(h_flex().w_full().justify_end()
        .child(div().text_size(T.small).text_color(c.dim).child(balance.resets))));
  }

  summary(c) {
    return v_flex().w_full().gap(G.sm)
      .child(h_flex().w_full().gap(G.lg).flex_wrap().items_stretch()
        .child(this.includedCard(c))
        .when(Boolean(this.report.balance), (el) => el.child(this.balanceCard(c))))
      .child(div().text_size(T.small).text_color(c.dim)
        .child("Included usage with your subscription. Used before your credits."));
  }

  hero(c) {
    const meter = this.report.meter;
    const account = this.report.account || {};
    const identity = [
      account.plan,
      account.email,
      account.since ? `on this Mac since ${account.since}` : "",
    ].filter(Boolean).join(" · ");
    const hot = Boolean(meter.blocked);
    return card(c)
      .child(h_flex().w_full().gap(G.md).items_center().justify_between()
        .child(div().text_size(T.label).font_medium().text_color(c.dim).child(meter.label))
        .when(Boolean(identity), (el) => el.child(div().flex_1().min_w(0).text_size(T.small).text_color(c.dim)
          .text_right().truncate().child(identity))))
      .child(div().text_size(T.metric).font_semibold().text_color(hot ? c.destructive : c.text).child(meter.value))
      .when(Boolean(meter.dollarsLeft), (el) => el.child(div().text_size(T.small).text_color(c.dim).child(meter.dollarsLeft)))
      .child(meterBar(meter.percentUsed, meter.elapsed, hot, this.barWidth, c)
        .on_size_change((size, sizeCx) => {
          if (Math.abs((this.barWidth || 0) - size.width) < 1) return;
          this.barWidth = size.width;
          sizeCx.notify();
        }))
      .when(Boolean(meter.when), (el) => el.child(div().text_size(T.small).text_color(c.dim).child(meter.when)))
      .when(Boolean(meter.pace), (el) => el.child(div().text_size(T.small)
        .text_color(meter.paceHot ? c.destructive : c.text).child(meter.pace)));
  }

  details(c) {
    const details = this.report.details || [];
    if (!details.length && !this.report.lastCommit) return null;
    return card(c)
      .when(details.length > 0, (el) => el.child(h_flex().w_full().gap(G.lg).children(
        details.map((item) => statCell(item.label, item.value, c)),
      )))
      .when(Boolean(this.report.lastCommit), (el) => el.child(div().text_size(T.small).text_color(c.dim)
        .child(`Last commit ${this.report.lastCommit} AI-generated`)));
  }

  tokens(c) {
    const tokens = this.report.tokens;
    if (!tokens || !(tokens.days || []).length) return null;
    const products = tokens.products || [];
    const days = tokens.days;
    const max = Math.max(...days.map((day) => (day.parts || []).reduce((sum, part) => sum + (Number(part.tokens) || 0), 0)), 1);
    const chart = 88;
    const tile = (label, value) => v_flex().flex_1().min_w(120).gap(G.xs).p(G.md).rounded(8).bg(c.subtle)
      .child(div().text_size(T.small).text_color(c.dim).child(label))
      .child(div().text_size(T.card).font_semibold().text_color(c.text).child(value));
    return card(c)
      .child(h_flex().w_full().gap(G.md).flex_wrap().children([
        tile("Total", `${tokens.total} tokens`),
        tile("Included", `${tokens.included} tokens`),
        tile("Credit usage", tokens.credit || "$0"),
      ]))
      .child(div().text_size(T.body).font_medium().text_color(c.text).child("Tokens per day"))
      .child(h_flex().w_full().h(chart + 16).gap(6).items_end().children(days.map((day, index) => {
        const parts = day.parts || [];
        const total = parts.reduce((sum, part) => sum + (Number(part.tokens) || 0), 0);
        const active = parts.filter((part) => part.tokens > 0);
        const tip = active.length
          ? `${shortDate(day.date)} · ${active.map((part) => {
            const named = products.find((item) => item.product === part.product);
            return `${named ? named.label : part.product} ${compactTokens(part.tokens)}`;
          }).join(" · ")}`
          : `${shortDate(day.date)} · No tokens`;
        const showLabel = days.length <= 14 || index === 0 || index === days.length - 1 || Number(day.date.slice(8)) % 5 === 0;
        return v_flex().flex_1().min_w(0).h(chart + 16).justify_end().gap(4).items_center().tooltip(tip)
          .child(v_flex().w_full().h(chart).justify_end().children(
            total === 0
              ? [div().w_full().h(1).bg(c.border)]
              : parts.filter((part) => part.tokens > 0).map((part) => div().w_full().rounded(2)
                .h(Math.max(Math.round(chart * (Number(part.tokens) || 0) / max), 2))
                .bg(SEGMENT_COLOR[part.product] || c.text)),
          ))
          .child(div().h(12).text_size(9).text_color(c.dim).child(showLabel ? shortDate(day.date) : ""));
      })))
      .child(h_flex().gap(G.md).flex_wrap().children(products.map((product) => h_flex().gap(6).items_center()
        .child(div().size(8).rounded_full().bg(SEGMENT_COLOR[product.product] || c.text))
        .child(div().text_size(T.small).text_color(c.mutedForeground).child(product.label)))))
      .child(div().text_size(T.body).font_medium().text_color(c.text).child("Usage by product"))
      .child(div().text_size(T.small).text_color(c.dim).child("For this month."))
      .child(v_flex().w_full().gap(G.sm).children([
        h_flex().w_full().items_center()
          .child(div().flex_1().text_size(T.small).text_color(c.dim).child("Product"))
          .child(div().w(88).text_right().text_size(T.small).text_color(c.dim).child("Tokens"))
          .child(div().w(72).text_right().text_size(T.small).text_color(c.dim).child("Spend")),
        ...products.map((product) => h_flex().w_full().items_center().gap(G.sm)
          .child(h_flex().flex_1().min_w(0).gap(6).items_center()
            .child(div().size(8).flex_none().rounded_full().bg(SEGMENT_COLOR[product.product] || c.text))
            .child(div().text_size(T.small).text_color(c.text).child(product.label)))
          .child(div().w(88).text_right().text_size(T.small).text_color(c.text).child(product.tokens))
          .child(div().w(72).text_right().text_size(T.small).text_color(c.dim).child(product.spend))),
      ]));
  }

  edits(c) {
    const rows = (this.report.edits || []).filter((day) => day && day.date);
    if (!rows.length) return null;
    const series = SERIES[this.series] ? this.series : "all";
    const spec = SERIES[series];
    const values = new Map(rows.map((day) => [day.date, Number(day[series]) || 0]));
    const weeks = yearWeeks(Date.now());
    const cells = [];
    let lastMonth = -1;
    const columns = weeks.map((days, index) => {
      const month = new Date(days[0]).getUTCMonth();
      const label = month === lastMonth ? "" : MONTHS[month];
      lastMonth = month;
      const squares = days.map((ms) => {
        const date = isoDay(ms);
        const value = values.get(date) || 0;
        cells.push({ date, value });
        return { date, value };
      });
      return { index, label, squares };
    });
    const max = Math.max(...cells.map((cell) => cell.value), 1);
    const stats = editStats(cells);
    const busiestMonth = stats.busiestMonth
      ? MONTH_NAMES[Number(stats.busiestMonth.slice(5, 7)) - 1]
      : "—";
    const busiestDay = stats.busiest
      ? `${shortDate(stats.busiest.date)}, ${stats.busiest.date.slice(0, 4)}`
      : "—";
    return card(c)
      .child(h_flex().w_full().items_start().justify_between().gap(G.md)
        .child(v_flex().gap(G.xs)
          .child(div().text_size(T.small).text_color(c.dim).child(spec.title))
          .child(div().text_size(T.metric).font_semibold().text_color(c.text)
            .child(stats.total.toLocaleString())))
        .child(h_flex().gap(2).p(2).rounded(8).bg(c.subtle).flex_none().children(
          Object.keys(SERIES).map((key) => {
            const on = key === series;
            return h_flex().id(`cursor-usage-series-${key}`).h(22).px(10).items_center().rounded(6)
              .cursor_pointer()
              .bg(on ? c.card : c.subtle)
              .border_1().border_color(on ? c.border : c.subtle)
              .on_click((_event, clickCx) => {
                this.series = key;
                clickCx.notify();
              })
              .child(div().text_size(T.small).text_color(on ? c.text : c.dim).child(SERIES[key].label));
          }),
        )))
      .child(v_flex().w_full().overflow_x_scrollbar().gap(G.xs)
        .child(div().relative().h(14).w(columns.length * (CELL + 3))
          .children(columns.filter((column) => column.label).map((column) => div()
            .absolute().top(0).left(column.index * (CELL + 3))
            .text_size(9).text_color(c.dim).child(column.label))))
        .child(h_flex().gap(3).items_start().children(columns.map((column) => v_flex().gap(3).flex_none()
          .children(column.squares.map((square) => {
            const level = heatLevel(square.value, max);
            const when = shortDate(square.date);
            const tip = square.value > 0
              ? `${when} · ${countPhrase(square.value, series)}`
              : `${when} · No ${spec.plural}`;
            return div().id(`cursor-usage-edit-${square.date}`).size(CELL).rounded(2).flex_none()
              .tooltip(tip)
              .bg(level === 0 ? c.border : HEAT[level - 1]);
          }))))))
      .child(h_flex().w_full().gap(G.lg).flex_wrap().children([
        ["Most active month", busiestMonth],
        ["Most active day", busiestDay],
        ["Longest streak", dayPhrase(stats.longest)],
        ["Current streak", dayPhrase(stats.current)],
      ].map(([label, value]) => statCell(label, value, c))))
      .child(h_flex().gap(6).items_center().children([
        div().text_size(T.small).text_color(c.dim).child("Less"),
        div().size(CELL).rounded(2).bg(c.border),
        ...HEAT.map((color, index) => div().id(`cursor-usage-heat-${index}`).size(CELL).rounded(2).bg(color)),
        div().text_size(T.small).text_color(c.dim).child("More"),
      ]))
      .when(Boolean(this.report.lastCommit), (el) => el.child(div().text_size(T.small).text_color(c.dim)
        .child(`Last commit ${this.report.lastCommit} AI-generated`)));
  }

  placeholder(c) {
    if (this.loading && !this.error) {
      return stateCard(c, { title: "Loading Cursor usage", body: "Reading the Cursor login on this Mac." });
    }
    return stateCard(c, {
      tone: c.destructive,
      title: "Cursor usage is unavailable",
      body: this.error || "Cursor did not return usage yet.",
      action: button("cursor-usage-retry", "Retry", (_event, clickCx) => this.refresh(clickCx), c, {
        variant: "primary",
        disabled: this.loading,
      }),
    });
  }

  render(_cx) {
    if (this.toolbarMode) return this.toolbar();
    const c = tokens(theme());
    const width = this.width || 720;
    const padding = width < 620 ? G.lg : G.xl;
    const cards = !this.report
      ? [this.placeholder(c)]
      : this.report.included
        ? [this.summary(c), this.tokens(c), this.edits(c)]
        : [this.hero(c), this.details(c), this.tokens(c), this.edits(c)];
    return v_flex().id("cursor-usage-root").size_full().min_h(0).bg(c.canvas).text_color(c.text)
      .on_size_change((size, sizeCx) => {
        const next = size.width / (Number(settings().zoom) || 1);
        if (Math.abs((this.width || 0) - next) < 1) return;
        this.width = next;
        sizeCx.notify();
      })
      .child(h_flex().w_full().justify_center().px(padding).pt(padding)
        .child(v_flex().w_full().max_w(920).child(this.header(c))))
      .child(v_flex().flex_1().min_h(0).overflow_y_scrollbar()
        .child(h_flex().w_full().justify_center().px(padding).pt(G.lg).pb(padding)
          .child(v_flex().w_full().max_w(920).gap(G.lg).children(cards.filter(Boolean)))));
  }
}
