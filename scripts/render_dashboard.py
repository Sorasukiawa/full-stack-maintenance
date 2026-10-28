#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Features:
- Real-time storage waterline simulator with red-line thresholds
- Baseline snapshot diff banner ("what grew since the last audit")
- A/B/C risk tier filtering and 4-domain + whitelist tab navigation
- localStorage persistence for checkboxes, per-item custom notes, and whitelist toggles
- One-click copy of structured execution instructions (IDs + custom notes + whitelist updates)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
from pathlib import Path
from typing import Any


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #0f141c;
    --bg-card: #171f2c;
    --bg-elev: #1e293b;
    --text: #f1f5f9;
    --text-muted: #94a3b8;
    --border: rgba(148, 163, 184, 0.18);
    --accent: #38bdf8;
    --green: #22c55e;
    --amber: #f59e0b;
    --red: #ef4444;
    --purple: #a855f7;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f8fafc;
      --bg-card: #ffffff;
      --bg-elev: #f1f5f9;
      --text: #0f172a;
      --text-muted: #475569;
      --border: rgba(15, 23, 42, 0.12);
      --accent: #0284c7;
      --green: #16a34a;
      --amber: #d97706;
      --red: #dc2626;
      --purple: #9333ea;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px 16px 120px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif;
    line-height: 1.5;
  }
  .container { max-width: 1120px; margin: 0 auto; }
  header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }
  h1 { margin: 0; font-size: 1.45rem; font-weight: 700; }
  .subtitle { margin: 4px 0 0; font-size: 0.88rem; color: var(--text-muted); }
  .section-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 18px;
    margin-bottom: 18px;
  }
  .section-title {
    margin: 0 0 12px;
    font-size: 0.96rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .gauge-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
  }
  .gauge-item {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
  }
  .gauge-head {
    display: flex;
    justify-content: space-between;
    font-size: 0.84rem;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .gauge-track {
    position: relative;
    height: 10px;
    background: rgba(148, 163, 184, 0.22);
    border-radius: 999px;
    overflow: hidden;
    margin-bottom: 6px;
  }
  .gauge-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 0.25s ease, background-color 0.25s ease;
  }
  .gauge-warn-line {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    background: var(--red);
    z-index: 2;
  }
  .gauge-meta {
    font-size: 0.76rem;
    color: var(--text-muted);
    display: flex;
    justify-content: space-between;
  }
  .diff-banner {
    border-left: 4px solid var(--accent);
  }
  .diff-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 8px;
    margin-top: 8px;
  }
  .diff-pill {
    background: var(--bg-elev);
    padding: 8px 10px;
    border-radius: 8px;
    font-size: 0.8rem;
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }
  .tabs, .quick-btns {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  button {
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 0.82rem;
    font-weight: 500;
    transition: all 0.15s ease;
  }
  button:hover { border-color: var(--accent); }
  button.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }
  .candidate-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .candidate-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }
  .candidate-card.checked {
    border-color: var(--green);
    background: rgba(34, 197, 94, 0.06);
  }
  .candidate-card.done {
    opacity: 0.72;
    border-color: var(--green);
  }
  .candidate-card.whitelisted {
    opacity: 0.78;
    border-style: dashed;
  }
  .candidate-body { flex: 1; min-width: 0; }
  .candidate-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .badge {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
  }
  .tier-A { background: rgba(34, 197, 94, 0.16); color: var(--green); }
  .tier-B { background: rgba(56, 189, 248, 0.16); color: var(--accent); }
  .tier-C { background: rgba(245, 158, 11, 0.16); color: var(--amber); }
  .badge-wl { background: rgba(168, 85, 247, 0.16); color: var(--purple); }
  .badge-done { background: rgba(34, 197, 94, 0.22); color: var(--green); }
  .candidate-title { font-weight: 600; font-size: 0.94rem; }
  .candidate-size {
    margin-left: auto;
    font-weight: 700;
    font-size: 0.88rem;
    color: var(--accent);
  }
  .candidate-path {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.76rem;
    color: var(--text-muted);
    word-break: break-all;
    margin-bottom: 4px;
  }
  .candidate-evidence {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-bottom: 8px;
  }
  .candidate-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .note-input {
    flex: 1;
    min-width: 220px;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 10px;
    border-radius: 7px;
    font-size: 0.8rem;
  }
  .sticky-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--bg-card);
    border-top: 1px solid var(--border);
    padding: 12px 20px;
    z-index: 50;
    box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.25);
  }
  .sticky-inner {
    max-width: 1120px;
    margin: 0 auto;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .primary-btn {
    background: var(--green);
    color: #fff;
    border-color: var(--green);
    font-weight: 600;
    padding: 8px 16px;
  }
  .modal-backdrop {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.65);
    z-index: 100;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    max-width: 720px;
    width: 100%;
    padding: 18px;
  }
  textarea.prompt-box {
    width: 100%;
    height: 220px;
    background: var(--bg-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    margin: 10px 0;
  }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>__HEADING__</h1>
      <p class="subtitle">生成时间：__GENERATED_AT__ ｜ 勾选下方项目将实时模拟顶部水位变化，刷新页面不会丢失勾选和备注</p>
    </div>
    <div class="quick-btns">
      <button onclick="selectByTier(['A'])">全选 A 级（安全立省）</button>
      <button onclick="selectByTier(['A', 'B'])">选 A + B 级</button>
      <button onclick="clearSelections()">清空勾选</button>
      <button onclick="resetSavedState()">重置缓存状态</button>
    </div>
  </header>

  <section class="section-card">
    <div class="section-title">
      <span>📊 核心存储池实时水位模拟器（勾选下方卡片动态预览释放效果）</span>
      <span id="sim-summary" style="font-size:0.82rem;color:var(--green)">预计释放：0.00 GiB</span>
    </div>
    <div class="gauge-grid" id="gauge-grid"></div>
  </section>

  <section class="section-card diff-banner" id="diff-section" style="display:none">
    <div class="section-title">
      <span>📈 距上次巡检基线变化（自动揪出是谁偷偷占了空间）</span>
      <span id="diff-time" style="font-size:0.78rem;color:var(--text-muted)"></span>
    </div>
    <div id="diff-gauges" class="diff-list"></div>
    <div id="diff-growth" class="diff-list" style="margin-top:8px"></div>
  </section>

  <div class="toolbar">
    <div class="tabs" id="domain-tabs">
      <button class="active" data-domain="all" onclick="setDomain('all')">全部项目</button>
      <button data-domain="mac" onclick="setDomain('mac')">💻 Mac 与备份</button>
      <button data-domain="projects" onclick="setDomain('projects')">📂 本地项目群</button>
      <button data-domain="github" onclick="setDomain('github')">🐙 GitHub 远端</button>
      <button data-domain="nas" onclick="setDomain('nas')">🏠 NAS 与 PVE</button>
      <button data-domain="whitelisted" onclick="setDomain('whitelisted')">🛡️ 已豁免白名单</button>
    </div>
  </div>

  <main class="candidate-list" id="candidate-list"></main>
</div>

<div class="sticky-bar">
  <div class="sticky-inner">
    <div>
      <strong id="selected-count">已勾选 0 项</strong>
      <span style="margin-left:12px;font-size:0.85rem;color:var(--text-muted)" id="selected-gib">预计释放 0.00 GiB</span>
    </div>
    <div class="quick-btns">
      <button class="primary-btn" onclick="copyExecutionPrompt()">📋 一键复制执行指令（含微调备注与白名单）</button>
    </div>
  </div>
</div>

<div class="modal-backdrop" id="prompt-modal">
  <div class="modal">
    <div class="section-title">
      <span>📋 已生成执行指令（可直接粘贴回对话框交给 AI 执行）</span>
      <button onclick="closeModal()">关闭</button>
    </div>
    <textarea class="prompt-box" id="prompt-textarea" readonly></textarea>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="primary-btn" onclick="copyModalText()">复制文本</button>
    </div>
  </div>
</div>

<script>
const REPORT_DATA = __REPORT_JSON__;
const STORAGE_KEY = "full_stack_maint_" + REPORT_DATA.report_id;

let currentDomain = "all";
let state = loadState();

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        checked: parsed.checked || {},
        notes: parsed.notes || {},
        whitelisted: parsed.whitelisted || {}
      };
    }
  } catch (e) {}
  const initialChecked = {};
  const initialWhitelisted = {};
  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.whitelisted) initialWhitelisted[c.id] = true;
    else if (c.default_checked || c.tier === "A") initialChecked[c.id] = true;
  });
  return { checked: initialChecked, notes: {}, whitelisted: initialWhitelisted };
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (e) {}
}

function formatBytes(bytes) {
  const b = Number(bytes) || 0;
  const abs = Math.abs(b);
  if (abs >= 1024 ** 3) return (b / (1024 ** 3)).toFixed(2) + " GiB";
  if (abs >= 1024 ** 2) return (b / (1024 ** 2)).toFixed(1) + " MiB";
  if (abs > 0) return (b / 1024).toFixed(0) + " KiB";
  return "不占空间 / 规则项";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function renderGauges() {
  const grid = document.getElementById("gauge-grid");
  grid.innerHTML = "";
  const savingsByGauge = {};
  let totalSavings = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done") return;
    if (state.whitelisted[c.id]) return;
    if (state.checked[c.id]) {
      const sz = Number(c.size_bytes) || 0;
      totalSavings += sz;
      const gid = c.gauge_id || "";
      if (gid) savingsByGauge[gid] = (savingsByGauge[gid] || 0) + sz;
    }
  });

  document.getElementById("sim-summary").textContent = "预计释放：" + formatBytes(totalSavings);

  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const saved = savingsByGauge[g.id] || 0;
    const newUsed = Math.max(0, used - saved);
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const newPct = total > 0 ? (newUsed / total) * 100 : curPct;
    const warnPct = Number(g.warn_used_pct) || 85;
    const color = newPct >= warnPct ? "var(--red)" : (newPct >= warnPct - 10 ? "var(--amber)" : "var(--green)");

    const card = document.createElement("div");
    card.className = "gauge-item";
    card.innerHTML = `
      <div class="gauge-head">
        <span>${escapeHtml(g.label || g.id)}</span>
        <span>${curPct.toFixed(1)}% → <strong>${newPct.toFixed(1)}%</strong></span>
      </div>
      <div class="gauge-track">
        <div class="gauge-warn-line" style="left:${Math.min(100, warnPct)}%" title="警戒水位 ${warnPct}%"></div>
        <div class="gauge-fill" style="width:${Math.min(100, newPct)}%;background:${color}"></div>
      </div>
      <div class="gauge-meta">
        <span>当前可用：${formatBytes(g.free_bytes || 0)}</span>
        <span>模拟释放：+${formatBytes(saved)}</span>
      </div>
    `;
    grid.appendChild(card);
  });
}

function renderDiff() {
  const diff = REPORT_DATA.diff;
  const sec = document.getElementById("diff-section");
  if (!diff || ((!diff.gauge_deltas || !diff.gauge_deltas.length) && (!diff.top_growth_paths || !diff.top_growth_paths.length))) {
    sec.style.display = "none";
    return;
  }
  sec.style.display = "block";
  document.getElementById("diff-time").textContent =
    `对比基线：${diff.before_generated_at || "上次"} → ${diff.after_generated_at || "本次"}`;

  const gBox = document.getElementById("diff-gauges");
  gBox.innerHTML = "";
  (diff.gauge_deltas || []).forEach(gd => {
    const sign = gd.used_delta_bytes > 0 ? "+" : "";
    const pill = document.createElement("div");
    pill.className = "diff-pill";
    pill.innerHTML = `<span>${escapeHtml(gd.label)}</span><strong>已用变化：${sign}${formatBytes(gd.used_delta_bytes)}</strong>`;
    gBox.appendChild(pill);
  });

  const pBox = document.getElementById("diff-growth");
  pBox.innerHTML = "";
  (diff.top_growth_paths || []).slice(0, 6).forEach(row => {
    const pill = document.createElement("div");
    pill.className = "diff-pill";
    const tag = row.is_new ? "[新增] " : "[增长] ";
    pill.innerHTML = `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(row.path)}">${tag}${escapeHtml(row.path)}</span><strong style="color:var(--amber)">+${formatBytes(row.delta_bytes)}</strong>`;
    pBox.appendChild(pill);
  });
}

function renderCandidates() {
  const list = document.getElementById("candidate-list");
  list.innerHTML = "";
  let checkedCount = 0;
  let checkedBytes = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    const isWl = !!state.whitelisted[c.id];
    const isDone = c.status === "done";
    const isChecked = !isWl && !isDone && !!state.checked[c.id];
    if (isChecked) {
      checkedCount++;
      checkedBytes += Number(c.size_bytes) || 0;
    }

    if (currentDomain === "whitelisted") {
      if (!isWl) return;
    } else {
      if (isWl) return;
      if (currentDomain !== "all" && c.domain !== currentDomain) return;
    }

    const tier = c.tier || "B";
    const tierLabel = tier === "A" ? "A级·安全立省" : (tier === "B" ? "B级·整理升级" : "C级·需拍板确认");
    const noteVal = state.notes[c.id] || c.whitelist_note || "";

    const card = document.createElement("div");
    card.className = "candidate-card" + (isChecked ? " checked" : "") + (isDone ? " done" : "") + (isWl ? " whitelisted" : "");
    card.innerHTML = `
      <input type="checkbox" style="margin-top:4px;width:18px;height:18px;cursor:pointer"
        ${isChecked ? "checked" : ""} ${isDone || isWl ? "disabled" : ""}
        onchange="toggleItem('${escapeHtml(c.id)}', this.checked)">
      <div class="candidate-body">
        <div class="candidate-top">
          <span class="badge tier-${escapeHtml(tier)}">${tierLabel}</span>
          ${isDone ? '<span class="badge badge-done">🎉 已完成</span>' : ""}
          ${isWl ? '<span class="badge badge-wl">🛡️ 已豁免白名单</span>' : ""}
          <span class="candidate-title">${escapeHtml(c.title)}</span>
          <span class="candidate-size">${formatBytes(c.size_bytes)}</span>
        </div>
        ${c.path ? `<div class="candidate-path">${escapeHtml(c.path)}</div>` : ""}
        <div class="candidate-evidence">${escapeHtml(c.evidence || "")}</div>
        <div class="candidate-controls">
          <input type="text" class="note-input" placeholder="✏️ 微调备注（如：留最近 3 个版本 / 移到 NAS 别直接删）"
            value="${escapeHtml(noteVal)}"
            oninput="updateNote('${escapeHtml(c.id)}', this.value)">
          <button onclick="toggleWhitelist('${escapeHtml(c.id)}')">
            ${isWl ? "取消豁免" : "🛡️ 长期豁免"}
          </button>
        </div>
      </div>
    `;
    list.appendChild(card);
  });

  document.getElementById("selected-count").textContent = `已勾选 ${checkedCount} 项`;
  document.getElementById("selected-gib").textContent = `预计释放 ${formatBytes(checkedBytes)}`;
  renderGauges();
}

function toggleItem(id, checked) {
  state.checked[id] = !!checked;
  saveState();
  renderCandidates();
}

function updateNote(id, val) {
  state.notes[id] = val;
  saveState();
}

function toggleWhitelist(id) {
  state.whitelisted[id] = !state.whitelisted[id];
  if (state.whitelisted[id]) state.checked[id] = false;
  saveState();
  renderCandidates();
}

function setDomain(domain) {
  currentDomain = domain;
  document.querySelectorAll("#domain-tabs button").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-domain") === domain);
  });
  renderCandidates();
}

function selectByTier(tiers) {
  const set = new Set(tiers);
  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    state.checked[c.id] = set.has(c.tier || "B");
  });
  saveState();
  renderCandidates();
}

function clearSelections() {
  state.checked = {};
  saveState();
  renderCandidates();
}

function resetSavedState() {
  localStorage.removeItem(STORAGE_KEY);
  state = loadState();
  renderCandidates();
}

function buildExecutionPrompt() {
  const selected = [];
  const newlyWhitelisted = [];
  (REPORT_DATA.candidates || []).forEach(c => {
    const note = (state.notes[c.id] || "").trim();
    if (state.whitelisted[c.id] && !c.whitelisted) {
      newlyWhitelisted.push({ id: c.id, title: c.title, path: c.path || "", note });
    } else if (state.checked[c.id] && c.status !== "done" && !state.whitelisted[c.id]) {
      selected.push({ id: c.id, title: c.title, path: c.path || "", note });
    }
  });

  const lines = [
    "请按我在全栈巡检 HTML 看板上的勾选执行清理与优化（动手前请先用 `full_stack_tool.py preflight` 复查并发活跃状态）：",
    ""
  ];
  if (selected.length === 0) {
    lines.push("- （本次未勾选待执行项）");
  } else {
    lines.push(`### 已勾选执行项（共 ${selected.length} 项）`);
    selected.forEach(item => {
      const pathPart = item.path ? ` (\`${item.path}\`)` : "";
      const notePart = item.note ? ` —— **微调备注：${item.note}**` : "";
      lines.push(`- [x] \`${item.id}\`: ${item.title}${pathPart}${notePart}`);
    });
  }
  if (newlyWhitelisted.length > 0) {
    lines.push("");
    lines.push("### 新增长期豁免白名单（请写入 `config/topology.local.json` 的 `whitelist`）");
    newlyWhitelisted.forEach(item => {
      const notePart = item.note ? `（备注：${item.note}）` : "";
      lines.push(`- 🛡️ \`${item.id}\`: ${item.path || item.title}${notePart}`);
    });
  }
  return lines.join("\\n");
}

function copyExecutionPrompt() {
  const text = buildExecutionPrompt();
  const modal = document.getElementById("prompt-modal");
  const box = document.getElementById("prompt-textarea");
  box.value = text;
  modal.classList.add("open");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
  }
}

function copyModalText() {
  const box = document.getElementById("prompt-textarea");
  box.select();
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(box.value).catch(() => {});
  } else {
    document.execCommand("copy");
  }
}

function closeModal() {
  document.getElementById("prompt-modal").classList.remove("open");
}

renderDiff();
renderCandidates();
</script>
</body>
</html>
"""


def render_dashboard_html(
    snapshot: dict[str, Any],
    diff: dict[str, Any] | None = None,
    title: str = "全栈健康巡检与空间水位交互看板",
) -> str:
    generated_at = str(snapshot.get("generated_at") or "")
    raw_id_seed = f"{generated_at}:{len(snapshot.get('candidates', []))}"
    report_id = snapshot.get("report_id") or hashlib.sha256(raw_id_seed.encode("utf-8")).hexdigest()[:12]

    payload = {
        "report_id": report_id,
        "generated_at": generated_at,
        "storage_gauges": snapshot.get("storage_gauges", []),
        "candidates": snapshot.get("candidates", []),
        "diff": diff if diff is not None else snapshot.get("diff"),
    }
    report_json = json.dumps(payload, ensure_ascii=False)
    # Escape closing script tags inside JSON string literals
    report_json = report_json.replace("</", "<\\/")

    out = HTML_TEMPLATE
    out = out.replace("__TITLE__", html.escape(title))
    out = out.replace("__HEADING__", html.escape(title))
    out = out.replace("__GENERATED_AT__", html.escape(generated_at or "刚刚"))
    out = out.replace("__REPORT_JSON__", report_json)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a self-contained HTML inspection dashboard.")
    parser.add_argument("--input", required=True, help="Path to snapshot/candidates JSON")
    parser.add_argument("--diff", help="Optional path to diff JSON from full_stack_tool.py diff")
    parser.add_argument("--title", default="全栈健康巡检与空间水位交互看板", help="Dashboard heading")
    parser.add_argument("--output", required=True, help="Output HTML file path")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing HTML file")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    snapshot = json.loads(input_path.read_text(encoding="utf-8"))
    diff_data = None
    if args.diff:
        diff_data = json.loads(Path(args.diff).read_text(encoding="utf-8"))

    html_text = render_dashboard_html(snapshot, diff=diff_data, title=args.title)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not args.overwrite:
        sys.stderr.write(f"ERROR: Output HTML already exists (pass --overwrite to replace): {out_path}\n")
        return 2
    out_path.write_text(html_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
