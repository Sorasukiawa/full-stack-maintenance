#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Visual & Motion Architecture (inspired by Linear, Raycast, Sonner, and Apple Fluid Interfaces):
- Translucent glassmorphic surfaces with top-edge specular highlights and ambient radial glow
- Real JS under-damped spring physics solver (velocity-preserving, interruptible) for storage gauges,
  rolling tabular numbers, and the sliding segmented-control tab pill
- Non-linear light-rebound CSS spring curves (--spring-rebound / --spring-soft) for custom SVG
  checkboxes, staggered card entrances, and the floating Dynamic-Island-style bottom dock
- Full localStorage persistence for checkboxes, per-item adjustment notes, and whitelist toggles
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
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
    --bg: #090d16;
    --bg-ambient: radial-gradient(ellipse 85% 42% at 50% -8%, rgba(56, 189, 248, 0.16), rgba(99, 102, 241, 0.08) 52%, transparent 80%);
    --surface: rgba(19, 27, 44, 0.72);
    --surface-elev: rgba(30, 41, 66, 0.68);
    --surface-hover: rgba(38, 52, 82, 0.78);
    --surface-checked: rgba(16, 48, 42, 0.68);
    --dock-bg: rgba(15, 23, 38, 0.82);
    --text: #f8fafc;
    --text-secondary: #cbd5e1;
    --text-muted: #8b9bb4;
    --border: rgba(148, 163, 184, 0.14);
    --border-highlight: rgba(255, 255, 255, 0.09);
    --accent: #38bdf8;
    --accent-glow: rgba(56, 189, 248, 0.28);
    --green: #10b981;
    --green-bright: #34d399;
    --green-glow: rgba(16, 185, 129, 0.28);
    --amber: #f59e0b;
    --red: #f43f5e;
    --purple: #a855f7;
    --shadow-card: 0 10px 30px -10px rgba(2, 6, 23, 0.55), inset 0 1px 0 var(--border-highlight);
    --shadow-dock: 0 20px 50px -12px rgba(2, 6, 23, 0.82), 0 0 0 1px rgba(255, 255, 255, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.16);
    /* Non-linear light rebound & fluid spring curves */
    --spring-rebound: cubic-bezier(0.34, 1.56, 0.64, 1);
    --spring-soft: cubic-bezier(0.22, 1.28, 0.36, 1);
    --ease-out-snappy: cubic-bezier(0.16, 1, 0.3, 1);
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f4f7fb;
      --bg-ambient: radial-gradient(ellipse 85% 40% at 50% -10%, rgba(14, 165, 233, 0.12), rgba(99, 102, 241, 0.05) 55%, transparent 80%);
      --surface: rgba(255, 255, 255, 0.84);
      --surface-elev: rgba(241, 245, 249, 0.88);
      --surface-hover: rgba(255, 255, 255, 0.96);
      --surface-checked: rgba(236, 253, 245, 0.9);
      --dock-bg: rgba(255, 255, 255, 0.86);
      --text: #0f172a;
      --text-secondary: #334155;
      --text-muted: #64748b;
      --border: rgba(15, 23, 42, 0.09);
      --border-highlight: rgba(255, 255, 255, 0.8);
      --accent: #0284c7;
      --accent-glow: rgba(2, 132, 199, 0.18);
      --green: #059669;
      --green-bright: #10b981;
      --green-glow: rgba(16, 185, 129, 0.2);
      --amber: #d97706;
      --red: #e11d48;
      --purple: #9333ea;
      --shadow-card: 0 8px 24px -8px rgba(15, 23, 42, 0.07), inset 0 1px 0 var(--border-highlight);
      --shadow-dock: 0 18px 42px -10px rgba(15, 23, 42, 0.18), 0 0 0 1px rgba(15, 23, 42, 0.08), inset 0 1px 0 #ffffff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 32px 18px 140px;
    background-color: var(--bg);
    background-image: var(--bg-ambient);
    background-attachment: fixed;
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "PingFang SC", "Hiragino Sans GB", sans-serif;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  .tabular { font-variant-numeric: tabular-nums; }
  .container { max-width: 1140px; margin: 0 auto; }

  /* Header */
  header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 24px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border);
  }
  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--accent);
    background: var(--surface-elev);
    border: 1px solid var(--border);
    padding: 3px 10px;
    border-radius: 999px;
    margin-bottom: 8px;
    box-shadow: inset 0 1px 0 var(--border-highlight);
  }
  .eyebrow-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green-bright);
    box-shadow: 0 0 8px var(--green-bright);
  }
  h1 {
    margin: 0;
    font-size: clamp(1.45rem, 2.4vw, 1.82rem);
    font-weight: 760;
    letter-spacing: -0.024em;
    line-height: 1.15;
  }
  .subtitle {
    margin: 6px 0 0;
    font-size: 0.87rem;
    color: var(--text-muted);
  }

  /* Buttons with immediate pointer-down response & spring release */
  button {
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--surface);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    color: var(--text);
    padding: 7px 13px;
    border-radius: 10px;
    font-size: 0.82rem;
    font-weight: 560;
    box-shadow: inset 0 1px 0 var(--border-highlight);
    transition:
      transform 240ms var(--spring-rebound),
      border-color 180ms var(--ease-out-snappy),
      background-color 180ms var(--ease-out-snappy),
      box-shadow 180ms var(--ease-out-snappy);
    user-select: none;
  }
  @media (hover: hover) and (pointer: fine) {
    button:hover {
      border-color: var(--accent);
      transform: translateY(-1px);
    }
  }
  button:active {
    transform: scale(0.95);
    transition-duration: 75ms;
  }
  .quick-btns {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  /* Glass Section Cards */
  .section-card {
    background: var(--surface);
    backdrop-filter: blur(22px) saturate(180%);
    -webkit-backdrop-filter: blur(22px) saturate(180%);
    border: 1px solid var(--border);
    border-radius: 18px;
    padding: 18px 20px;
    margin-bottom: 18px;
    box-shadow: var(--shadow-card);
  }
  .section-title {
    margin: 0 0 14px;
    font-size: 0.95rem;
    font-weight: 650;
    letter-spacing: -0.01em;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .sim-pill {
    font-size: 0.82rem;
    font-weight: 680;
    color: var(--green-bright);
    background: rgba(16, 185, 129, 0.12);
    border: 1px solid rgba(16, 185, 129, 0.28);
    padding: 4px 12px;
    border-radius: 999px;
    transition: transform 320ms var(--spring-rebound);
  }
  .sim-pill.bump {
    transform: scale(1.08);
  }

  /* Waterline Gauges */
  .gauge-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(235px, 1fr));
    gap: 12px;
  }
  .gauge-item {
    background: var(--surface-elev);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px 15px;
    box-shadow: inset 0 1px 0 var(--border-highlight);
    transition: transform 280ms var(--spring-soft), border-color 220ms ease;
  }
  @media (hover: hover) and (pointer: fine) {
    .gauge-item:hover {
      transform: translateY(-2px);
      border-color: rgba(56, 189, 248, 0.35);
    }
  }
  .gauge-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    font-size: 0.83rem;
    font-weight: 620;
    margin-bottom: 8px;
  }
  .gauge-pct {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
  }
  .gauge-track {
    position: relative;
    height: 11px;
    background: rgba(148, 163, 184, 0.18);
    border-radius: 999px;
    overflow: hidden;
    margin-bottom: 8px;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.25);
  }
  .gauge-fill {
    height: 100%;
    border-radius: 999px;
    will-change: width;
    transition: background-color 280ms ease;
    position: relative;
  }
  .gauge-fill::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.22));
    border-radius: 999px;
  }
  .gauge-warn-line {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    background: var(--red);
    box-shadow: 0 0 6px var(--red);
    z-index: 2;
  }
  .gauge-meta {
    font-size: 0.76rem;
    color: var(--text-muted);
    display: flex;
    justify-content: space-between;
  }

  /* Diff Banner */
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
    background: var(--surface-elev);
    border: 1px solid var(--border);
    padding: 8px 12px;
    border-radius: 10px;
    font-size: 0.8rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }

  /* Segmented Control Domain Tabs with Spring Sliding Pill */
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
  }
  .segmented-tabs {
    position: relative;
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 5px;
    background: var(--surface);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid var(--border);
    border-radius: 14px;
    box-shadow: var(--shadow-card);
  }
  .tab-glider {
    position: absolute;
    top: 5px;
    left: 0;
    height: calc(100% - 10px);
    background: var(--accent);
    border-radius: 10px;
    box-shadow: 0 4px 14px var(--accent-glow);
    z-index: 0;
    pointer-events: none;
    will-change: transform, width;
  }
  .segmented-tabs button {
    position: relative;
    z-index: 1;
    border: none;
    background: transparent;
    box-shadow: none;
    padding: 7px 14px;
    border-radius: 10px;
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text-secondary);
    transition: color 180ms ease, transform 160ms var(--spring-rebound);
  }
  .segmented-tabs button:hover {
    transform: none;
    color: var(--text);
  }
  .segmented-tabs button:active {
    transform: scale(0.95);
  }
  .segmented-tabs button.active {
    color: #ffffff;
  }

  /* Candidate Cards with Non-Linear Spring Entrance & Tactile Checkboxes */
  .candidate-list {
    display: flex;
    flex-direction: column;
    gap: 11px;
  }
  @keyframes cardSpringIn {
    0% {
      opacity: 0;
      transform: translateY(12px) scale(0.982);
      filter: blur(2px);
    }
    70% {
      opacity: 1;
      transform: translateY(-1.5px) scale(1.003);
      filter: blur(0);
    }
    100% {
      opacity: 1;
      transform: translateY(0) scale(1);
      filter: blur(0);
    }
  }
  .candidate-card {
    background: var(--surface);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    border: 1px solid var(--border);
    border-radius: 15px;
    padding: 15px 18px;
    display: flex;
    align-items: flex-start;
    gap: 14px;
    box-shadow: var(--shadow-card);
    animation: cardSpringIn 360ms var(--spring-soft) both;
    transition:
      transform 280ms var(--spring-soft),
      border-color 200ms var(--ease-out-snappy),
      background-color 220ms var(--ease-out-snappy),
      box-shadow 220ms var(--ease-out-snappy);
  }
  @media (hover: hover) and (pointer: fine) {
    .candidate-card:hover {
      transform: translateY(-2px);
      border-color: rgba(148, 163, 184, 0.32);
    }
  }
  .candidate-card.checked {
    border-color: rgba(16, 185, 129, 0.55);
    background: var(--surface-checked);
    box-shadow: 0 12px 32px -12px var(--green-glow), inset 0 1px 0 var(--border-highlight);
  }
  .candidate-card.pulse {
    transform: scale(1.012);
  }
  .candidate-card.done {
    opacity: 0.72;
    border-color: var(--green);
  }
  .candidate-card.whitelisted {
    opacity: 0.82;
    border-style: dashed;
    border-color: rgba(168, 85, 247, 0.45);
  }

  /* Custom Spring SVG Checkbox */
  .check-wrap {
    position: relative;
    width: 22px;
    height: 22px;
    margin-top: 2px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .check-wrap input {
    position: absolute;
    opacity: 0;
    width: 100%;
    height: 100%;
    margin: 0;
    cursor: pointer;
    z-index: 2;
  }
  .check-box {
    width: 22px;
    height: 22px;
    border-radius: 7px;
    border: 1.8px solid var(--text-muted);
    background: var(--surface-elev);
    display: flex;
    align-items: center;
    justify-content: center;
    transition:
      transform 320ms var(--spring-rebound),
      background-color 180ms ease,
      border-color 180ms ease,
      box-shadow 220ms ease;
  }
  .check-wrap:active .check-box {
    transform: scale(0.86);
    transition-duration: 70ms;
  }
  .check-wrap input:checked + .check-box {
    background: var(--green);
    border-color: var(--green-bright);
    transform: scale(1.06);
    box-shadow: 0 0 12px var(--green-glow);
  }
  .check-svg {
    width: 13px;
    height: 13px;
    fill: none;
    stroke: #ffffff;
    stroke-width: 2.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 20;
    stroke-dashoffset: 20;
    transition: stroke-dashoffset 280ms var(--spring-rebound);
  }
  .check-wrap input:checked + .check-box .check-svg {
    stroke-dashoffset: 0;
  }

  .candidate-body { flex: 1; min-width: 0; }
  .candidate-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
  }
  .badge {
    font-size: 0.72rem;
    font-weight: 660;
    padding: 2px 9px;
    border-radius: 999px;
    transition: transform 240ms var(--spring-rebound);
  }
  .tier-A { background: rgba(16, 185, 129, 0.16); color: var(--green-bright); border: 1px solid rgba(16, 185, 129, 0.28); }
  .tier-B { background: rgba(56, 189, 248, 0.16); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.28); }
  .tier-C { background: rgba(245, 158, 11, 0.16); color: var(--amber); border: 1px solid rgba(245, 158, 11, 0.28); }
  .badge-wl { background: rgba(168, 85, 247, 0.16); color: var(--purple); border: 1px solid rgba(168, 85, 247, 0.3); }
  .badge-done { background: rgba(16, 185, 129, 0.22); color: var(--green-bright); }
  .candidate-title {
    font-weight: 650;
    font-size: 0.95rem;
    letter-spacing: -0.01em;
    cursor: pointer;
  }
  .candidate-size {
    margin-left: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-weight: 720;
    font-size: 0.88rem;
    color: var(--accent);
  }
  .candidate-path {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.76rem;
    color: var(--text-muted);
    word-break: break-all;
    margin-bottom: 5px;
  }
  .candidate-evidence {
    font-size: 0.83rem;
    color: var(--text-secondary);
    margin-bottom: 10px;
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
    background: var(--surface-elev);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 11px;
    border-radius: 9px;
    font-size: 0.8rem;
    transition: border-color 180ms ease, box-shadow 220ms var(--spring-soft), transform 200ms var(--spring-soft);
  }
  .note-input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
    transform: translateY(-1px);
  }

  /* Floating Dynamic-Island Glass Dock */
  .floating-dock-wrap {
    position: fixed;
    bottom: 20px;
    left: 0;
    right: 0;
    display: flex;
    justify-content: center;
    padding: 0 16px;
    z-index: 50;
    pointer-events: none;
  }
  .floating-dock {
    pointer-events: auto;
    max-width: 880px;
    width: 100%;
    background: var(--dock-bg);
    backdrop-filter: blur(26px) saturate(190%);
    -webkit-backdrop-filter: blur(26px) saturate(190%);
    border-radius: 20px;
    padding: 12px 18px;
    box-shadow: var(--shadow-dock);
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    transition: transform 340ms var(--spring-rebound);
  }
  .floating-dock.dock-bump {
    transform: translateY(-3px) scale(1.012);
  }
  .dock-stats {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .dock-count-badge {
    background: var(--surface-elev);
    border: 1px solid var(--border);
    padding: 4px 11px;
    border-radius: 999px;
    font-size: 0.84rem;
    font-weight: 700;
    transition: transform 300ms var(--spring-rebound);
  }
  .primary-btn {
    background: linear-gradient(135deg, var(--green), var(--green-bright));
    color: #ffffff;
    border-color: rgba(255, 255, 255, 0.22);
    font-weight: 660;
    padding: 9px 18px;
    border-radius: 12px;
    box-shadow: 0 6px 20px -4px var(--green-glow), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  }
  @media (hover: hover) and (pointer: fine) {
    .primary-btn:hover {
      border-color: #ffffff;
      transform: translateY(-1.5px) scale(1.01);
      box-shadow: 0 10px 26px -4px var(--green-glow), inset 0 1px 0 rgba(255, 255, 255, 0.45);
    }
  }
  .primary-btn:active {
    transform: scale(0.95);
  }

  /* Spring Modal */
  .modal-backdrop {
    display: flex;
    position: fixed;
    inset: 0;
    background: rgba(2, 6, 23, 0.62);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    z-index: 100;
    align-items: center;
    justify-content: center;
    padding: 16px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 220ms ease;
  }
  .modal-backdrop.open {
    opacity: 1;
    pointer-events: auto;
  }
  .modal {
    background: var(--surface);
    backdrop-filter: blur(28px) saturate(190%);
    -webkit-backdrop-filter: blur(28px) saturate(190%);
    border: 1px solid var(--border);
    border-radius: 20px;
    max-width: 720px;
    width: 100%;
    padding: 20px;
    box-shadow: var(--shadow-dock);
    transform: scale(0.92) translateY(16px);
    transition: transform 360ms var(--spring-rebound);
  }
  .modal-backdrop.open .modal {
    transform: scale(1) translateY(0);
  }
  textarea.prompt-box {
    width: 100%;
    height: 220px;
    background: var(--surface-elev);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    margin: 10px 0 14px;
    resize: vertical;
  }

  /* Reduced Motion Accessibility */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 120ms !important;
      transition-timing-function: ease !important;
    }
  }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <div class="eyebrow">
        <span class="eyebrow-dot"></span>
        <span>FULL-STACK MAINTENANCE · WATERLINE SIMULATOR</span>
      </div>
      <h1>__HEADING__</h1>
      <p class="subtitle">生成时间：__GENERATED_AT__ ｜ 勾选下方项目将实时弹性模拟顶部水位变化，刷新页面不会丢失勾选和备注</p>
    </div>
    <div class="quick-btns">
      <button onclick="selectByTier(['A'])">✨ 全选 A 级（安全立省）</button>
      <button onclick="selectByTier(['A', 'B'])">⚡ 选 A + B 级</button>
      <button onclick="clearSelections()">清空勾选</button>
      <button onclick="resetSavedState()">重置缓存状态</button>
    </div>
  </header>

  <section class="section-card">
    <div class="section-title">
      <span>📊 核心存储池实时水位模拟器（勾选下方卡片体验非线性轻回弹水位动态预览）</span>
      <span id="sim-summary" class="sim-pill tabular">预计释放：0.00 GiB</span>
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
    <div class="segmented-tabs" id="domain-tabs">
      <div class="tab-glider" id="tab-glider"></div>
      <button class="active" data-domain="all" onclick="setDomain('all', this)">全部项目</button>
      <button data-domain="mac" onclick="setDomain('mac', this)">💻 Mac 与备份</button>
      <button data-domain="projects" onclick="setDomain('projects', this)">📂 本地项目群</button>
      <button data-domain="github" onclick="setDomain('github', this)">🐙 GitHub 远端</button>
      <button data-domain="nas" onclick="setDomain('nas', this)">🏠 NAS 与 PVE</button>
      <button data-domain="whitelisted" onclick="setDomain('whitelisted', this)">🛡️ 已豁免白名单</button>
    </div>
  </div>

  <main class="candidate-list" id="candidate-list"></main>
</div>

<div class="floating-dock-wrap">
  <div class="floating-dock" id="floating-dock">
    <div class="dock-stats">
      <span class="dock-count-badge tabular" id="selected-count">已勾选 0 项</span>
      <span class="tabular" style="font-size:0.88rem;color:var(--text-secondary)" id="selected-gib">预计释放 0.00 GiB</span>
    </div>
    <div class="quick-btns">
      <button class="primary-btn" onclick="copyExecutionPrompt()">📋 一键复制执行指令（含微调备注与白名单）</button>
    </div>
  </div>
</div>

<div class="modal-backdrop" id="prompt-modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="section-title">
      <span>📋 已生成执行指令（可直接粘贴回对话框交给 AI 执行）</span>
      <button onclick="closeModal()">关闭</button>
    </div>
    <textarea class="prompt-box" id="prompt-textarea" readonly></textarea>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="primary-btn" id="copy-confirm-btn" onclick="copyModalText()">复制文本</button>
    </div>
  </div>
</div>

<script>
const REPORT_DATA = __REPORT_JSON__;
const STORAGE_KEY = "full_stack_maint_" + REPORT_DATA.report_id;
const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Interruptible Under-Damped Harmonic Spring Solver (Apple Fluid Interface style)
 * Preserves live velocity when retargeted mid-flight and settles with a subtle light rebound.
 */
class SpringNumber {
  constructor(initialValue, onUpdate, config = {}) {
    this.current = initialValue;
    this.target = initialValue;
    this.velocity = 0;
    this.stiffness = config.stiffness || 240;
    this.damping = config.damping || 19; // damping ratio ~0.61 -> crisp non-linear light rebound
    this.mass = config.mass || 1;
    this.onUpdate = onUpdate;
    this.rafId = null;
    this.lastTime = 0;
  }
  set(newTarget) {
    if (REDUCED_MOTION) {
      this.current = newTarget;
      this.target = newTarget;
      this.velocity = 0;
      this.onUpdate(this.current);
      return;
    }
    this.target = newTarget;
    if (!this.rafId) {
      this.lastTime = performance.now();
      this.rafId = requestAnimationFrame(ts => this._step(ts));
    }
  }
  _step(now) {
    const dt = Math.min((now - this.lastTime) / 1000, 0.032);
    this.lastTime = now;
    const displacement = this.current - this.target;
    const springForce = -this.stiffness * displacement;
    const dampingForce = -this.damping * this.velocity;
    const acceleration = (springForce + dampingForce) / this.mass;
    this.velocity += acceleration * dt;
    this.current += this.velocity * dt;

    if (Math.abs(this.velocity) < 0.02 && Math.abs(this.target - this.current) < 0.02) {
      this.current = this.target;
      this.velocity = 0;
      this.rafId = null;
      this.onUpdate(this.current);
      return;
    }
    this.onUpdate(this.current);
    this.rafId = requestAnimationFrame(ts => this._step(ts));
  }
}

let currentDomain = "all";
let state = loadState();
const gaugeSprings = {};
let totalSavingsSpring = null;
let tabGliderSpringX = null;
let tabGliderSpringW = null;

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
  return "规则项";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function pulseElement(el, cls = "bump", ms = 260) {
  if (!el || REDUCED_MOTION) return;
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

function initGaugesDOM() {
  const grid = document.getElementById("gauge-grid");
  grid.innerHTML = "";
  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const freeBytes = g.free_bytes != null ? Number(g.free_bytes) : Math.max(0, total - used);
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const warnPct = Number(g.warn_used_pct || g.warn_pct) || 85;

    const card = document.createElement("div");
    card.className = "gauge-item";
    card.innerHTML = `
      <div class="gauge-head">
        <span>${escapeHtml(g.label || g.id)}</span>
        <span class="gauge-pct tabular">${curPct.toFixed(1)}% → <strong id="gpct-${escapeHtml(g.id)}">${curPct.toFixed(1)}%</strong></span>
      </div>
      <div class="gauge-track">
        <div class="gauge-warn-line" style="left:${Math.min(100, warnPct)}%" title="警戒水位 ${warnPct}%"></div>
        <div class="gauge-fill" id="gfill-${escapeHtml(g.id)}" style="width:${Math.min(100, curPct)}%"></div>
      </div>
      <div class="gauge-meta tabular">
        <span>当前可用：${formatBytes(freeBytes)}</span>
        <span id="gsaved-${escapeHtml(g.id)}">模拟释放：+0.00 GiB</span>
      </div>
    `;
    grid.appendChild(card);

    gaugeSprings[g.id] = new SpringNumber(curPct, val => {
      const clamped = Math.max(0, Math.min(100, val));
      const fillEl = document.getElementById("gfill-" + g.id);
      const pctEl = document.getElementById("gpct-" + g.id);
      if (fillEl) {
        fillEl.style.width = clamped.toFixed(2) + "%";
        fillEl.style.backgroundColor =
          clamped >= warnPct ? "var(--red)" : (clamped >= warnPct - 10 ? "var(--amber)" : "var(--green-bright)");
      }
      if (pctEl) pctEl.textContent = clamped.toFixed(1) + "%";
    });
  });

  totalSavingsSpring = new SpringNumber(0, val => {
    const safeVal = Math.max(0, val);
    const simEl = document.getElementById("sim-summary");
    const dockEl = document.getElementById("selected-gib");
    if (simEl) simEl.textContent = "预计释放：" + formatBytes(safeVal);
    if (dockEl) dockEl.textContent = "预计释放 " + formatBytes(safeVal);
  });
}

function updateGaugesTargets() {
  const savingsByGauge = {};
  let totalSavings = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    if (state.checked[c.id]) {
      const sz = Number(c.size_bytes) || 0;
      totalSavings += sz;
      const gid = c.gauge_id || "";
      if (gid) savingsByGauge[gid] = (savingsByGauge[gid] || 0) + sz;
    }
  });

  if (totalSavingsSpring) totalSavingsSpring.set(totalSavings);
  pulseElement(document.getElementById("sim-summary"), "bump", 260);

  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const saved = savingsByGauge[g.id] || 0;
    const newUsed = Math.max(0, used - saved);
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const newPct = total > 0 ? (newUsed / total) * 100 : curPct;

    if (gaugeSprings[g.id]) gaugeSprings[g.id].set(newPct);
    const savedEl = document.getElementById("gsaved-" + g.id);
    if (savedEl) savedEl.textContent = "模拟释放：+" + formatBytes(saved);
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
    pill.className = "diff-pill tabular";
    pill.innerHTML = `<span>${escapeHtml(gd.label)}</span><strong>已用变化：${sign}${formatBytes(gd.used_delta_bytes)}</strong>`;
    gBox.appendChild(pill);
  });

  const pBox = document.getElementById("diff-growth");
  pBox.innerHTML = "";
  (diff.top_growth_paths || []).slice(0, 6).forEach(row => {
    const pill = document.createElement("div");
    pill.className = "diff-pill tabular";
    const tag = row.is_new ? "[新增] " : "[增长] ";
    pill.innerHTML = `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(row.path)}">${tag}${escapeHtml(row.path)}</span><strong style="color:var(--amber)">+${formatBytes(row.delta_bytes)}</strong>`;
    pBox.appendChild(pill);
  });
}

function updateSelectionTotals() {
  let checkedCount = 0;
  (REPORT_DATA.candidates || []).forEach(c => {
    if (!state.whitelisted[c.id] && c.status !== "done" && state.checked[c.id]) {
      checkedCount++;
    }
  });
  const countEl = document.getElementById("selected-count");
  if (countEl) countEl.textContent = `已勾选 ${checkedCount} 项`;
  pulseElement(document.getElementById("floating-dock"), "dock-bump", 280);
  updateGaugesTargets();
}

function renderCandidates() {
  const list = document.getElementById("candidate-list");
  list.innerHTML = "";
  let visibleIndex = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    const isWl = !!state.whitelisted[c.id];
    const isDone = c.status === "done";
    const isChecked = !isWl && !isDone && !!state.checked[c.id];

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
    card.id = "card-" + c.id;
    card.className = "candidate-card" + (isChecked ? " checked" : "") + (isDone ? " done" : "") + (isWl ? " whitelisted" : "");
    card.style.animationDelay = `${Math.min(visibleIndex * 28, 220)}ms`;
    visibleIndex++;

    card.innerHTML = `
      <label class="check-wrap" title="勾选/取消">
        <input type="checkbox" id="chk-${escapeHtml(c.id)}"
          ${isChecked ? "checked" : ""} ${isDone || isWl ? "disabled" : ""}
          onchange="toggleItem('${escapeHtml(c.id)}', this.checked)">
        <span class="check-box">
          <svg class="check-svg" viewBox="0 0 16 16"><polyline points="3.5 8.5 6.8 11.8 12.8 4.5"></polyline></svg>
        </span>
      </label>
      <div class="candidate-body">
        <div class="candidate-top">
          <span class="badge tier-${escapeHtml(tier)}">${tierLabel}</span>
          ${isDone ? '<span class="badge badge-done">🎉 已完成</span>' : ""}
          ${isWl ? '<span class="badge badge-wl">🛡️ 已豁免白名单</span>' : ""}
          <span class="candidate-title" onclick="clickCardTitle('${escapeHtml(c.id)}')">${escapeHtml(c.title)}</span>
          <span class="candidate-size tabular">${formatBytes(c.size_bytes)}</span>
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

  updateSelectionTotals();
}

function clickCardTitle(id) {
  const chk = document.getElementById("chk-" + id);
  if (!chk || chk.disabled) return;
  chk.checked = !chk.checked;
  toggleItem(id, chk.checked);
}

function toggleItem(id, checked) {
  state.checked[id] = !!checked;
  saveState();
  const card = document.getElementById("card-" + id);
  if (card) {
    card.classList.toggle("checked", !!checked);
    pulseElement(card, "pulse", 220);
  }
  updateSelectionTotals();
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

function moveTabGlider(activeBtn) {
  const glider = document.getElementById("tab-glider");
  if (!glider || !activeBtn) return;
  const x = activeBtn.offsetLeft;
  const w = activeBtn.offsetWidth;
  if (!tabGliderSpringX) {
    tabGliderSpringX = new SpringNumber(x, val => {
      glider.style.transform = `translate3d(${val.toFixed(1)}px, 0, 0)`;
    }, { stiffness: 280, damping: 21 });
    tabGliderSpringW = new SpringNumber(w, val => {
      glider.style.width = `${Math.max(20, val).toFixed(1)}px`;
    }, { stiffness: 280, damping: 22 });
    glider.style.transform = `translate3d(${x}px, 0, 0)`;
    glider.style.width = `${w}px`;
  } else {
    tabGliderSpringX.set(x);
    tabGliderSpringW.set(w);
  }
}

function setDomain(domain, btnEl) {
  currentDomain = domain;
  let targetBtn = btnEl;
  document.querySelectorAll("#domain-tabs button").forEach(btn => {
    const isAct = btn.getAttribute("data-domain") === domain;
    btn.classList.toggle("active", isAct);
    if (isAct && !targetBtn) targetBtn = btn;
  });
  if (targetBtn) moveTabGlider(targetBtn);
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
  const btn = document.getElementById("copy-confirm-btn");
  box.select();
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(box.value).catch(() => {});
  } else {
    document.execCommand("copy");
  }
  if (btn) {
    const old = btn.textContent;
    btn.textContent = "✅ 已复制到剪贴板";
    setTimeout(() => { btn.textContent = old; }, 1400);
  }
}

function closeModal() {
  document.getElementById("prompt-modal").classList.remove("open");
}

window.addEventListener("resize", () => {
  const activeBtn = document.querySelector("#domain-tabs button.active");
  if (activeBtn) moveTabGlider(activeBtn);
});

initGaugesDOM();
renderDiff();
renderCandidates();
requestAnimationFrame(() => {
  const activeBtn = document.querySelector("#domain-tabs button.active");
  if (activeBtn) moveTabGlider(activeBtn);
});
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
