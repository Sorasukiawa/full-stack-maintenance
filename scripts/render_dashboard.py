#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Design System & Motion Architecture:
- Inspired by Anthropic's editorial design language: warm ivory paper (#FAF9F5), deep charcoal ink
  (#141413), muted stone secondary surfaces (#F2F0E8), hairline borders (#E6E4DC), and restrained
  terracotta (#C96442) & forest sage (#2E6B4E) accents. Zero neon gradients or emoji clutter.
- Typography pairs editorial serif headings with humanist sans-serif body copy and strict
  tabular-nums monospace metrics (non-wrapping single-line figures).
- Motion uses an exact closed-form analytical underdamped harmonic oscillator (damping ratio
  zeta = 0.72, ~3.8% peak overshoot) that is 100% frame-rate independent and immune to Euler
  integration blowups, driving storage waterline bars, rolling figures, and the sliding tab pill.
- Full localStorage persistence for checkboxes, per-item adjustment notes, and whitelist toggles.
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
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
  :root {
    /* Anthropic Editorial Warm Paper Palette (Light Default) */
    --bg-page: #FAF9F5;
    --bg-recessed: #F1EFE7;
    --bg-card: #FFFFFF;
    --bg-card-hover: #FCFBF8;
    --bg-card-selected: #FBF6F3;
    --bg-sticky: rgba(250, 249, 245, 0.92);
    --ink-primary: #141413;
    --ink-secondary: #52514C;
    --ink-muted: #87857E;
    --border-hairline: #E6E4DC;
    --border-strong: #CFCBC0;
    --accent-clay: #C96442;
    --accent-clay-soft: rgba(201, 100, 66, 0.12);
    --tone-sage: #2E6B4E;
    --tone-sage-bg: #EBF3EE;
    --tone-ochre: #B56A1E;
    --tone-ochre-bg: #F8EFE4;
    --tone-rust: #B83B2A;
    --tone-rust-bg: #FBECE9;
    --shadow-subtle: 0 1px 2px rgba(20, 20, 19, 0.04);
    --shadow-modal: 0 24px 48px -12px rgba(20, 20, 19, 0.16);

    /* Typography */
    --font-serif: "Newsreader", "Instrument Serif", "Iowan Old Style", "Charter", "Songti SC", "STSong", Georgia, serif;
    --font-sans: -apple-system, BlinkMacSystemFont, "Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --font-mono: "SF Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

    /* Non-linear light rebound curves (restrained ~3.5% overshoot, no cartoon bounce) */
    --spring-rebound: cubic-bezier(0.28, 1.28, 0.56, 1);
    --spring-soft: cubic-bezier(0.22, 1.18, 0.38, 1);
    --ease-out-clean: cubic-bezier(0.16, 1, 0.3, 1);
  }

  [data-theme="dark"] {
    --bg-page: #141413;
    --bg-recessed: #1C1B19;
    --bg-card: #201F1D;
    --bg-card-hover: #252421;
    --bg-card-selected: #26201D;
    --bg-sticky: rgba(20, 20, 19, 0.92);
    --ink-primary: #FAF9F5;
    --ink-secondary: #B5B2A8;
    --ink-muted: #7D7A72;
    --border-hairline: #2E2D29;
    --border-strong: #42403A;
    --accent-clay: #D97757;
    --accent-clay-soft: rgba(217, 119, 87, 0.16);
    --tone-sage: #52A37A;
    --tone-sage-bg: rgba(82, 163, 122, 0.14);
    --tone-ochre: #D99242;
    --tone-ochre-bg: rgba(217, 146, 66, 0.14);
    --tone-rust: #E06352;
    --tone-rust-bg: rgba(224, 99, 82, 0.15);
    --shadow-subtle: 0 1px 3px rgba(0, 0, 0, 0.25);
    --shadow-modal: 0 24px 48px -12px rgba(0, 0, 0, 0.65);
  }

  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    background-color: var(--bg-page);
    color: var(--ink-primary);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    transition: background-color 220ms ease, color 220ms ease;
  }
  .tabular {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .nowrap {
    white-space: nowrap;
  }

  /* Top Sticky Editorial Command Bar (Never overlaps content) */
  .top-bar {
    position: sticky;
    top: 0;
    z-index: 40;
    background: var(--bg-sticky);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border-hairline);
  }
  .top-bar-inner {
    max-width: 1200px;
    margin: 0 auto;
    padding: 12px 24px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .brand-cluster {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-mark {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent-clay);
    flex-shrink: 0;
  }
  .brand-meta {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-muted);
  }
  .action-cluster {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .summary-stat {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 5px 12px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    font-size: 12.5px;
    color: var(--ink-secondary);
    transition: transform 280ms var(--spring-rebound);
  }
  .summary-stat.rebound {
    transform: scale(1.035);
  }
  .summary-stat strong {
    color: var(--ink-primary);
    font-weight: 600;
  }

  /* Buttons: Crisp, Editorial, Tactile, No Ugly Native Focus Box */
  button {
    appearance: none;
    cursor: pointer;
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-primary);
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 5px 10px;
    line-height: 1.35;
    box-shadow: var(--shadow-subtle);
    outline: none;
    transition:
      transform 220ms var(--spring-rebound),
      border-color 160ms ease,
      background-color 160ms ease,
      color 160ms ease;
    user-select: none;
  }
  button:focus {
    outline: none;
  }
  button:focus-visible {
    outline: 1.5px solid var(--accent-clay);
    outline-offset: 1px;
  }
  @media (hover: hover) and (pointer: fine) {
    button:hover {
      border-color: var(--border-strong);
      background: var(--bg-card-hover);
    }
  }
  button:active {
    transform: scale(0.96);
    transition-duration: 60ms;
  }
  .btn-primary {
    background: var(--ink-primary);
    color: var(--bg-page);
    border-color: var(--ink-primary);
    font-weight: 550;
    padding: 6px 14px;
  }
  @media (hover: hover) and (pointer: fine) {
    .btn-primary:hover {
      background: var(--accent-clay);
      border-color: var(--accent-clay);
      color: #FAF9F5;
    }
  }
  .btn-ghost {
    background: transparent;
    box-shadow: none;
    color: var(--ink-secondary);
  }

  /* Page Container & Editorial Header */
  .container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 36px 24px 96px;
  }
  .editorial-header {
    margin-bottom: 32px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--border-hairline);
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-end;
    gap: 20px;
  }
  .editorial-kicker {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--accent-clay);
    margin-bottom: 8px;
  }
  h1 {
    font-family: var(--font-serif);
    font-size: clamp(26px, 3vw, 34px);
    font-weight: 600;
    letter-spacing: -0.015em;
    line-height: 1.2;
    margin: 0 0 8px;
    color: var(--ink-primary);
  }
  .editorial-subtitle {
    margin: 0;
    font-size: 14px;
    color: var(--ink-secondary);
    max-width: 680px;
  }

  /* Section Headers */
  .section-block {
    margin-bottom: 32px;
  }
  .section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }
  .section-title {
    font-family: var(--font-serif);
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0;
    color: var(--ink-primary);
  }
  .section-caption {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--ink-muted);
  }

  /* 5-Pool Balanced 3+2 Bento Matrix (Generous breathing room, zero cramped text or orphan gaps) */
  .gauge-grid {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 12px;
  }
  .gauge-grid .gauge-card {
    grid-column: span 2;
  }
  .gauge-grid .gauge-card:nth-child(4),
  .gauge-grid .gauge-card:nth-child(5) {
    grid-column: span 3;
  }
  @media (max-width: 680px) {
    .gauge-grid {
      grid-template-columns: 1fr;
    }
    .gauge-grid .gauge-card,
    .gauge-grid .gauge-card:nth-child(4),
    .gauge-grid .gauge-card:nth-child(5) {
      grid-column: span 1;
    }
  }
  .gauge-card {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 15px 16px;
    box-shadow: var(--shadow-subtle);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
    overflow: hidden;
    transition: transform 260ms var(--spring-soft), border-color 180ms ease;
  }
  @media (hover: hover) and (pointer: fine) {
    .gauge-card:hover {
      transform: translateY(-1.5px);
      border-color: var(--border-strong);
    }
  }
  .gauge-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .gauge-index {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    letter-spacing: 0.04em;
  }
  .status-pip {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    padding: 2px 7px;
    border-radius: 4px;
    white-space: nowrap;
  }
  .pip-crit { background: var(--tone-rust-bg); color: var(--tone-rust); }
  .pip-warn { background: var(--tone-ochre-bg); color: var(--tone-ochre); }
  .pip-ok { background: var(--tone-sage-bg); color: var(--tone-sage); }

  .gauge-name {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 2px;
  }
  .gauge-Readout {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
    white-space: nowrap;
  }
  .gauge-pct-Transition {
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ink-primary);
  }
  .gauge-pct-before {
    font-size: 12px;
    font-weight: 400;
    color: var(--ink-muted);
    text-decoration: line-through;
    margin-right: 4px;
  }
  .gauge-track {
    position: relative;
    height: 6px;
    background: var(--bg-recessed);
    border-radius: 999px;
    overflow: hidden;
  }
  .gauge-fill-ghost {
    position: absolute;
    top: 0;
    left: 0;
    bottom: 0;
    background: var(--border-strong);
    opacity: 0.45;
    border-radius: 999px;
  }
  .gauge-fill {
    position: relative;
    height: 100%;
    border-radius: 999px;
    will-change: width;
    transition: background-color 240ms ease;
  }
  .gauge-warn-tick {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1.5px;
    background: var(--ink-primary);
    opacity: 0.35;
    z-index: 2;
  }
  .gauge-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-secondary);
    white-space: nowrap;
    padding-top: 8px;
    border-top: 1px solid var(--border-hairline);
  }
  .gauge-saved-tag {
    color: var(--tone-sage);
    font-weight: 600;
  }

  /* Baseline Diff Ledger */
  .diff-card {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 16px 20px;
  }
  .diff-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-top: 10px;
  }
  #diff-growth.diff-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  @media (max-width: 920px) {
    .diff-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 600px) {
    .diff-grid,
    #diff-growth.diff-grid {
      grid-template-columns: 1fr;
    }
  }
  .diff-item {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
    padding: 10px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 12.5px;
    min-width: 0;
    overflow: hidden;
  }
  .diff-item-label {
    color: var(--ink-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 1;
  }
  .diff-item-val {
    font-family: var(--font-mono);
    font-weight: 600;
    white-space: nowrap;
    flex-shrink: 0;
  }

  /* Segmented Filter Bar with 2D (X/Y/W/H) Spring Pill Glider */
  .filter-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 14px;
  }
  .segmented-control {
    position: relative;
    display: inline-flex;
    flex-wrap: wrap;
    gap: 2px;
    padding: 3px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
  }
  .tab-glider {
    position: absolute;
    top: 0;
    left: 0;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 5px;
    box-shadow: var(--shadow-subtle);
    z-index: 0;
    pointer-events: none;
    will-change: transform, width, height;
  }
  .segmented-control button {
    position: relative;
    z-index: 1;
    border: none;
    background: transparent;
    box-shadow: none;
    padding: 4px 10px;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-secondary);
    outline: none;
    transition: color 160ms ease, transform 160ms var(--spring-rebound);
  }
  .segmented-control button:focus,
  .segmented-control button:focus-visible {
    outline: none;
  }
  .segmented-control button:hover {
    color: var(--ink-primary);
    background: transparent;
  }
  .segmented-control button.active {
    color: var(--ink-primary);
    font-weight: 600;
  }
  .tab-count {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-muted);
    margin-left: 3px;
  }

  /* Theme Toggle Pill in Top Bar */
  .theme-switch {
    display: inline-flex;
    padding: 2px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    gap: 2px;
  }
  .theme-switch button {
    border: none;
    background: transparent;
    box-shadow: none;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11.5px;
    color: var(--ink-muted);
  }
  .theme-switch button.active {
    background: var(--bg-card);
    color: var(--ink-primary);
    font-weight: 600;
    box-shadow: var(--shadow-subtle);
  }

  /* Candidate List & Editorial Cards */
  .candidate-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  @keyframes editorialSlideIn {
    0% {
      opacity: 0;
      transform: translateY(8px);
    }
    70% {
      opacity: 1;
      transform: translateY(-1px);
    }
    100% {
      opacity: 1;
      transform: translateY(0);
    }
  }
  .candidate-card {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 13px 16px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    box-shadow: var(--shadow-subtle);
    animation: editorialSlideIn 280ms var(--spring-soft) both;
    transition:
      transform 240ms var(--spring-soft),
      border-color 160ms ease,
      background-color 180ms ease;
  }
  @media (hover: hover) and (pointer: fine) {
    .candidate-card:hover {
      border-color: var(--border-strong);
    }
  }
  .candidate-card.checked {
    background: var(--bg-card-selected);
    border-color: rgba(201, 100, 66, 0.42);
  }
  .candidate-card.pulse {
    transform: scale(1.006);
  }
  .candidate-card.done {
    opacity: 0.65;
  }
  .candidate-card.whitelisted {
    opacity: 0.75;
    border-style: dashed;
  }

  /* Sleek 15px Tactile Checkbox with Non-Linear Spring Checkmark */
  .check-wrap {
    position: relative;
    width: 15px;
    height: 15px;
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
    width: 15px;
    height: 15px;
    border-radius: 3.5px;
    border: 1.5px solid var(--border-strong);
    background: var(--bg-card);
    display: flex;
    align-items: center;
    justify-content: center;
    transition:
      transform 240ms var(--spring-rebound),
      background-color 150ms ease,
      border-color 150ms ease;
  }
  .check-wrap:active .check-box {
    transform: scale(0.88);
    transition-duration: 55ms;
  }
  .check-wrap input:checked + .check-box {
    background: var(--accent-clay);
    border-color: var(--accent-clay);
    transform: scale(1.03);
  }
  .check-svg {
    width: 9.5px;
    height: 9.5px;
    fill: none;
    stroke: #FAF9F5;
    stroke-width: 2.3;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 18;
    stroke-dashoffset: 18;
    transition: stroke-dashoffset 220ms var(--spring-rebound);
  }
  .check-wrap input:checked + .check-box .check-svg {
    stroke-dashoffset: 0;
  }

  .candidate-body {
    flex: 1;
    min-width: 0;
  }
  .candidate-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    margin-bottom: 4px;
  }
  .item-id {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
  }
  .tier-tag {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 500;
    padding: 1px 7px;
    border-radius: 4px;
  }
  .tier-A { background: var(--tone-sage-bg); color: var(--tone-sage); }
  .tier-B { background: var(--accent-clay-soft); color: var(--accent-clay); }
  .tier-C { background: var(--tone-ochre-bg); color: var(--tone-ochre); }
  .candidate-title {
    font-weight: 600;
    font-size: 14px;
    color: var(--ink-primary);
    cursor: pointer;
  }
  .candidate-size {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
  }
  .candidate-path {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--ink-muted);
    word-break: break-all;
    margin-bottom: 4px;
  }
  .candidate-evidence {
    font-size: 13px;
    color: var(--ink-secondary);
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
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    color: var(--ink-primary);
    padding: 5px 10px;
    border-radius: 5px;
    font-family: var(--font-sans);
    font-size: 12px;
    transition: border-color 150ms ease, background-color 150ms ease;
  }
  .note-input::placeholder {
    color: var(--ink-muted);
  }
  .note-input:focus {
    outline: none;
    background: var(--bg-card);
    border-color: var(--accent-clay);
  }

  /* Modal Dialog */
  .modal-backdrop {
    display: flex;
    position: fixed;
    inset: 0;
    background: rgba(20, 20, 19, 0.45);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 100;
    align-items: center;
    justify-content: center;
    padding: 20px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 180ms ease;
  }
  .modal-backdrop.open {
    opacity: 1;
    pointer-events: auto;
  }
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    max-width: 680px;
    width: 100%;
    padding: 24px;
    box-shadow: var(--shadow-modal);
    transform: scale(0.95) translateY(10px);
    transition: transform 280ms var(--spring-rebound);
  }
  .modal-backdrop.open .modal {
    transform: scale(1) translateY(0);
  }
  textarea.prompt-box {
    width: 100%;
    height: 220px;
    background: var(--bg-recessed);
    color: var(--ink-primary);
    border: 1px solid var(--border-hairline);
    border-radius: 8px;
    padding: 12px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.5;
    margin: 12px 0 16px;
    resize: vertical;
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 80ms !important;
      transition-timing-function: ease !important;
    }
  }
</style>
</head>
<body>

<header class="top-bar">
  <div class="top-bar-inner">
    <div class="brand-cluster">
      <span class="brand-mark"></span>
      <span class="brand-meta">FULL-STACK MAINTENANCE · INSPECTION LEDGER</span>
    </div>
    <div class="action-cluster">
      <div class="summary-stat tabular" id="sticky-summary">
        <span>已选 <strong id="selected-count">0</strong> 项</span>
        <span style="color:var(--border-strong)">|</span>
        <span>预计释放 <strong id="selected-gib" style="color:var(--tone-sage)">0.00 GiB</strong></span>
      </div>
      <button onclick="selectByTier(['A'])">全选 A 级（零风险）</button>
      <button onclick="selectByTier(['A', 'B'])">选推荐方案 (A+B)</button>
      <button onclick="clearSelections()">清空</button>
      <div class="theme-switch" role="group" aria-label="外观模式切换">
        <button type="button" id="theme-dark-btn" class="active" onclick="setTheme('dark')">深色</button>
        <button type="button" id="theme-light-btn" onclick="setTheme('light')">浅色</button>
      </div>
      <button class="btn-primary" onclick="copyExecutionPrompt()">导出执行指令</button>
    </div>
  </div>
</header>

<div class="container">
  <div class="editorial-header">
    <div>
      <div class="editorial-kicker">SYSTEM &amp; REPOSITORY HEALTH REPORT</div>
      <h1>__HEADING__</h1>
      <p class="editorial-subtitle">
        记录时间：__GENERATED_AT__。勾选下方条目可实时模拟顶部存储池水位变化；所有勾选状态、微调备注与豁免项均自动保存在本地浏览器。
      </p>
    </div>
    <div>
      <button class="btn-ghost" onclick="resetSavedState()">重置本地缓存状态</button>
    </div>
  </div>

  <section class="section-block">
    <div class="section-header">
      <h2 class="section-title">核心存储池水位模拟</h2>
      <span class="section-caption">勾选下方清理项后，水位条与剩余百分比将按欠阻尼弹簧物理实时回落</span>
    </div>
    <div class="gauge-grid" id="gauge-grid"></div>
  </section>

  <section class="section-block" id="diff-section" style="display:none">
    <div class="section-header">
      <h2 class="section-title">距上次巡检基线变化</h2>
      <span class="section-caption" id="diff-time"></span>
    </div>
    <div class="diff-card">
      <div id="diff-gauges" class="diff-grid" style="margin-top:0"></div>
      <div id="diff-growth" class="diff-grid"></div>
    </div>
  </section>

  <section class="section-block">
    <div class="section-header">
      <h2 class="section-title">可执行优化与清理清单</h2>
      <span class="section-caption" id="visible-count-caption"></span>
    </div>

    <div class="filter-bar">
      <div class="segmented-control" id="domain-tabs" role="tablist">
        <div class="tab-glider" id="tab-glider"></div>
        <button class="active" data-domain="all" onclick="setDomain('all', this)">全部 <span class="tab-count" id="cnt-all"></span></button>
        <button data-domain="mac" onclick="setDomain('mac', this)">Mac 与备份 <span class="tab-count" id="cnt-mac"></span></button>
        <button data-domain="projects" onclick="setDomain('projects', this)">本地项目 <span class="tab-count" id="cnt-projects"></span></button>
        <button data-domain="github" onclick="setDomain('github', this)">GitHub <span class="tab-count" id="cnt-github"></span></button>
        <button data-domain="nas" onclick="setDomain('nas', this)">NAS 与 PVE <span class="tab-count" id="cnt-nas"></span></button>
        <button data-domain="whitelisted" onclick="setDomain('whitelisted', this)">白名单 <span class="tab-count" id="cnt-whitelisted"></span></button>
      </div>
    </div>

    <main class="candidate-list" id="candidate-list"></main>
  </section>
</div>

<div class="modal-backdrop" id="prompt-modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="section-header" style="margin-bottom:4px">
      <h2 class="section-title">已生成执行指令</h2>
      <button onclick="closeModal()">关闭</button>
    </div>
    <p style="margin:0;font-size:12.5px;color:var(--ink-secondary)">
      以下文本已自动复制到剪贴板，可直接粘贴回对话框交给 Agent 执行：
    </p>
    <textarea class="prompt-box" id="prompt-textarea" readonly></textarea>
    <div style="display:flex;justify-content:flex-end;gap:8px">
      <button class="btn-primary" id="copy-confirm-btn" onclick="copyModalText()">复制指令文本</button>
    </div>
  </div>
</div>

<script>
const REPORT_DATA = __REPORT_JSON__;
const STORAGE_KEY = "full_stack_maint_" + REPORT_DATA.report_id;
const THEME_KEY = "full_stack_maint_theme";
const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Closed-form Analytical Underdamped Spring Evaluator.
 * Unlike Euler integration (which can overshoot wildly on frame drops), this evaluates the exact
 * analytical solution of an underdamped harmonic oscillator (zeta = 0.72, omega0 = 15, ~3.8% light
 * rebound) so values never go out of bounds and always settle cleanly.
 */
class AnalyticalSpring {
  constructor(initialValue, onUpdate, config = {}) {
    this.current = initialValue;
    this.startVal = initialValue;
    this.target = initialValue;
    this.zeta = config.zeta || 0.72; // ~3.8% subtle light rebound
    this.omega0 = config.omega0 || 15.0;
    this.omegaD = this.omega0 * Math.sqrt(1 - this.zeta * this.zeta);
    this.durationMs = config.durationMs || 460;
    this.onUpdate = onUpdate;
    this.rafId = null;
    this.startTime = 0;
  }
  set(newTarget) {
    if (REDUCED_MOTION || Math.abs(newTarget - this.current) < 0.001) {
      this.current = newTarget;
      this.startVal = newTarget;
      this.target = newTarget;
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
      this.onUpdate(this.current);
      return;
    }
    this.startVal = this.current;
    this.target = newTarget;
    this.startTime = performance.now();
    if (!this.rafId) {
      this.rafId = requestAnimationFrame(ts => this._step(ts));
    }
  }
  _step(now) {
    const elapsedMs = Math.max(0, now - this.startTime);
    if (elapsedMs >= this.durationMs) {
      this.current = this.target;
      this.rafId = null;
      this.onUpdate(this.current);
      return;
    }
    const t = elapsedMs / 1000;
    const decay = Math.exp(-this.zeta * this.omega0 * t);
    const cosPart = Math.cos(this.omegaD * t);
    const sinPart = (this.zeta / Math.sqrt(1 - this.zeta * this.zeta)) * Math.sin(this.omegaD * t);
    const progress = 1 - decay * (cosPart + sinPart);
    this.current = this.startVal + (this.target - this.startVal) * progress;
    this.onUpdate(this.current);
    this.rafId = requestAnimationFrame(ts => this._step(ts));
  }
}

let currentDomain = "all";
let state = loadState();
const gaugeSprings = {};
let totalSavingsSpring = null;
let tabGliderSpringX = null;
let tabGliderSpringY = null;
let tabGliderSpringW = null;
let tabGliderSpringH = null;
let tabGliderX = 0;
let tabGliderY = 0;

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const theme = saved === "light" ? "light" : "dark";
  applyTheme(theme);
}

function applyTheme(theme) {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const darkBtn = document.getElementById("theme-dark-btn");
  const lightBtn = document.getElementById("theme-light-btn");
  if (darkBtn) darkBtn.classList.toggle("active", theme === "dark");
  if (lightBtn) lightBtn.classList.toggle("active", theme !== "dark");
}

function setTheme(theme) {
  localStorage.setItem(THEME_KEY, theme);
  applyTheme(theme);
}

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

function stripLeadingEmoji(str) {
  return String(str || "").replace(/^[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}\\u{FE0F}\\s]+/u, "").trim();
}

function formatBytes(bytes) {
  const b = Number(bytes) || 0;
  const abs = Math.abs(b);
  if (abs >= 1024 ** 4) return (b / (1024 ** 4)).toFixed(2) + " TiB";
  if (abs >= 1024 ** 3) return (b / (1024 ** 3)).toFixed(2) + " GiB";
  if (abs >= 1024 ** 2) return (b / (1024 ** 2)).toFixed(1) + " MiB";
  if (abs > 0) return (b / 1024).toFixed(0) + " KiB";
  return "规则与配置";
}

function formatCompactBytes(bytes) {
  const b = Number(bytes) || 0;
  const abs = Math.abs(b);
  if (abs >= 1024 ** 4) return (b / (1024 ** 4)).toFixed(1) + " TiB";
  if (abs >= 1024 ** 3) return (b / (1024 ** 3)).toFixed(1) + " GiB";
  if (abs >= 1024 ** 2) return (b / (1024 ** 2)).toFixed(0) + " MiB";
  if (abs > 0) return (b / 1024).toFixed(0) + " KiB";
  return "0 GiB";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function pulseElement(el, cls = "rebound", ms = 240) {
  if (!el || REDUCED_MOTION) return;
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

function initGaugesDOM() {
  const grid = document.getElementById("gauge-grid");
  grid.innerHTML = "";
  (REPORT_DATA.storage_gauges || []).forEach((g, idx) => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const freeBytes = g.free_bytes != null ? Number(g.free_bytes) : Math.max(0, total - used);
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const warnPct = Number(g.warn_used_pct || g.warn_pct) || 85;
    const cleanLabel = stripLeadingEmoji(g.label || g.id);
    const indexStr = String(idx + 1).padStart(2, "0");

    const card = document.createElement("div");
    card.className = "gauge-card";
    card.innerHTML = `
      <div>
        <div class="gauge-top">
          <span class="gauge-index">POOL ${indexStr} · ${formatCompactBytes(total)}</span>
          <span class="status-pip" id="gpip-${escapeHtml(g.id)}"></span>
        </div>
        <div class="gauge-name" title="${escapeHtml(cleanLabel)}">${escapeHtml(cleanLabel)}</div>
      </div>
      <div>
        <div class="gauge-Readout">
          <div class="gauge-pct-Transition tabular">
            <span class="gauge-pct-before" id="gbefore-${escapeHtml(g.id)}">${curPct.toFixed(1)}%</span>
            <span id="gpct-${escapeHtml(g.id)}">${curPct.toFixed(1)}%</span>
          </div>
          <span class="gauge-saved-tag mono" style="font-size:11.5px" id="gsaved-${escapeHtml(g.id)}">+0.0 GiB</span>
        </div>
        <div class="gauge-track" style="margin-top:8px">
          <div class="gauge-fill-ghost" style="width:${Math.min(100, curPct).toFixed(2)}%"></div>
          <div class="gauge-warn-tick" style="left:${Math.min(100, warnPct)}%" title="警戒线 ${warnPct}%"></div>
          <div class="gauge-fill" id="gfill-${escapeHtml(g.id)}" style="width:${Math.min(100, curPct).toFixed(2)}%"></div>
        </div>
      </div>
      <div class="gauge-foot tabular">
        <span>模拟后可用</span>
        <strong id="gfree-${escapeHtml(g.id)}" style="color:var(--ink-primary)">${formatCompactBytes(freeBytes)}</strong>
      </div>
    `;
    grid.appendChild(card);

    gaugeSprings[g.id] = new AnalyticalSpring(curPct, val => {
      const clamped = Math.max(0, Math.min(100, val));
      const fillEl = document.getElementById("gfill-" + g.id);
      const pctEl = document.getElementById("gpct-" + g.id);
      const pipEl = document.getElementById("gpip-" + g.id);
      const isCrit = clamped >= warnPct;
      const isWarn = !isCrit && clamped >= warnPct - 10;

      if (fillEl) {
        fillEl.style.width = clamped.toFixed(2) + "%";
        fillEl.style.backgroundColor = isCrit ? "var(--tone-rust)" : (isWarn ? "var(--tone-ochre)" : "var(--tone-sage)");
      }
      if (pctEl) pctEl.textContent = clamped.toFixed(1) + "%";
      if (pipEl) {
        pipEl.className = "status-pip " + (isCrit ? "pip-crit" : (isWarn ? "pip-warn" : "pip-ok"));
        pipEl.textContent = isCrit ? "水位告警" : (isWarn ? "接近阈值" : "水位健康");
      }
    });
  });

  totalSavingsSpring = new AnalyticalSpring(0, val => {
    const safeGiB = Math.max(0, val);
    const dockEl = document.getElementById("selected-gib");
    if (dockEl) dockEl.textContent = safeGiB.toFixed(2) + " GiB";
  });
}

function updateGaugesTargets() {
  const savingsByGauge = {};
  let totalSavingsBytes = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    if (state.checked[c.id]) {
      const sz = Number(c.size_bytes) || 0;
      totalSavingsBytes += sz;
      const gid = c.gauge_id || "";
      if (gid) savingsByGauge[gid] = (savingsByGauge[gid] || 0) + sz;
    }
  });

  if (totalSavingsSpring) {
    totalSavingsSpring.set(totalSavingsBytes / (1024 ** 3));
  }
  pulseElement(document.getElementById("sticky-summary"), "rebound", 240);

  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const baseFree = g.free_bytes != null ? Number(g.free_bytes) : Math.max(0, total - used);
    const saved = savingsByGauge[g.id] || 0;
    const newUsed = Math.max(0, used - saved);
    const newFree = baseFree + saved;
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const newPct = total > 0 ? (newUsed / total) * 100 : curPct;

    if (gaugeSprings[g.id]) gaugeSprings[g.id].set(newPct);
    const beforeEl = document.getElementById("gbefore-" + g.id);
    if (beforeEl) {
      beforeEl.style.display = saved > 0 ? "inline" : "none";
    }
    const freeEl = document.getElementById("gfree-" + g.id);
    if (freeEl) freeEl.textContent = formatCompactBytes(newFree);
    const savedEl = document.getElementById("gsaved-" + g.id);
    if (savedEl) {
      savedEl.textContent = saved > 0 ? ("释放 +" + formatCompactBytes(saved)) : "无待释放";
      savedEl.style.color = saved > 0 ? "var(--tone-sage)" : "var(--ink-muted)";
    }
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
    `${diff.before_generated_at || "上次快照"} → ${diff.after_generated_at || "当前快照"}`;

  const gBox = document.getElementById("diff-gauges");
  gBox.innerHTML = "";
  (diff.gauge_deltas || []).forEach(gd => {
    const delta = Number(gd.used_delta_bytes) || 0;
    const sign = delta > 0 ? "+" : "";
    const color = delta <= 0 ? "var(--tone-sage)" : "var(--tone-ochre)";
    const item = document.createElement("div");
    item.className = "diff-item tabular";
    item.innerHTML = `
      <span class="diff-item-label">${escapeHtml(stripLeadingEmoji(gd.label))}</span>
      <span class="diff-item-val" style="color:${color}">${sign}${formatBytes(delta)}</span>
    `;
    gBox.appendChild(item);
  });

  const pBox = document.getElementById("diff-growth");
  pBox.innerHTML = "";
  (diff.top_growth_paths || []).slice(0, 6).forEach(row => {
    const item = document.createElement("div");
    item.className = "diff-item tabular";
    const tag = row.is_new ? "新增" : "变动";
    item.innerHTML = `
      <span class="diff-item-label" title="${escapeHtml(row.path)}">[${tag}] ${escapeHtml(row.path)}</span>
      <span class="diff-item-val" style="color:var(--accent-clay)">${formatBytes(row.delta_bytes)}</span>
    `;
    pBox.appendChild(item);
  });
}

function updateTabCounts() {
  const counts = { all: 0, mac: 0, projects: 0, github: 0, nas: 0, whitelisted: 0 };
  (REPORT_DATA.candidates || []).forEach(c => {
    if (state.whitelisted[c.id]) {
      counts.whitelisted++;
    } else {
      counts.all++;
      if (counts[c.domain] != null) counts[c.domain]++;
    }
  });
  Object.keys(counts).forEach(k => {
    const el = document.getElementById("cnt-" + k);
    if (el) el.textContent = String(counts[k]);
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
  if (countEl) countEl.textContent = String(checkedCount);
  updateTabCounts();
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
    const tierLabel = tier === "A" ? "A 级 · 零风险可重建" : (tier === "B" ? "B 级 · 推荐整理" : "C 级 · 需确认时机");
    const noteVal = state.notes[c.id] || c.whitelist_note || "";

    const card = document.createElement("div");
    card.id = "card-" + c.id;
    card.className = "candidate-card" + (isChecked ? " checked" : "") + (isDone ? " done" : "") + (isWl ? " whitelisted" : "");
    card.style.animationDelay = `${Math.min(visibleIndex * 22, 180)}ms`;
    visibleIndex++;

    card.innerHTML = `
      <label class="check-wrap" title="勾选或取消">
        <input type="checkbox" id="chk-${escapeHtml(c.id)}"
          ${isChecked ? "checked" : ""} ${isDone || isWl ? "disabled" : ""}
          onchange="toggleItem('${escapeHtml(c.id)}', this.checked)">
        <span class="check-box">
          <svg class="check-svg" viewBox="0 0 16 16"><polyline points="3.5 8.5 6.8 11.8 12.8 4.5"></polyline></svg>
        </span>
      </label>
      <div class="candidate-body">
        <div class="candidate-head">
          <span class="item-id">${escapeHtml(c.id)}</span>
          <span class="tier-tag tier-${escapeHtml(tier)}">${tierLabel}</span>
          ${isDone ? '<span class="tier-tag tier-A">已执行</span>' : ""}
          ${isWl ? '<span class="tier-tag tier-C">已豁免</span>' : ""}
          <span class="candidate-size tabular">${formatBytes(c.size_bytes)}</span>
        </div>
        <div class="candidate-title" style="margin-bottom:4px" onclick="clickCardTitle('${escapeHtml(c.id)}')">${escapeHtml(stripLeadingEmoji(c.title))}</div>
        ${c.path ? `<div class="candidate-path">${escapeHtml(c.path)}</div>` : ""}
        <div class="candidate-evidence">${escapeHtml(c.evidence || "")}</div>
        <div class="candidate-controls">
          <input type="text" class="note-input" placeholder="添加执行微调说明（如：保留最近 2 个版本 / 移至归档目录）"
            value="${escapeHtml(noteVal)}"
            oninput="updateNote('${escapeHtml(c.id)}', this.value)">
          <button class="btn-ghost" onclick="toggleWhitelist('${escapeHtml(c.id)}')">
            ${isWl ? "移出白名单" : "加入白名单"}
          </button>
        </div>
      </div>
    `;
    list.appendChild(card);
  });

  const capEl = document.getElementById("visible-count-caption");
  if (capEl) capEl.textContent = `当前显示 ${visibleIndex} 项`;
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
    pulseElement(card, "pulse", 200);
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
  requestAnimationFrame(() => {
    const activeBtn = document.querySelector("#domain-tabs button.active");
    if (activeBtn) moveTabGlider(activeBtn);
  });
}

function moveTabGlider(activeBtn) {
  const glider = document.getElementById("tab-glider");
  if (!glider || !activeBtn) return;
  const x = activeBtn.offsetLeft;
  const y = activeBtn.offsetTop;
  const w = activeBtn.offsetWidth;
  const h = activeBtn.offsetHeight;

  const updateTransform = () => {
    glider.style.transform = `translate3d(${tabGliderX.toFixed(1)}px, ${tabGliderY.toFixed(1)}px, 0)`;
  };

  if (!tabGliderSpringX) {
    tabGliderX = x;
    tabGliderY = y;
    tabGliderSpringX = new AnalyticalSpring(x, val => {
      tabGliderX = val;
      updateTransform();
    }, { zeta: 0.74, omega0: 17, durationMs: 360 });
    tabGliderSpringY = new AnalyticalSpring(y, val => {
      tabGliderY = val;
      updateTransform();
    }, { zeta: 0.78, omega0: 17, durationMs: 360 });
    tabGliderSpringW = new AnalyticalSpring(w, val => {
      glider.style.width = `${Math.max(20, val).toFixed(1)}px`;
    }, { zeta: 0.78, omega0: 17, durationMs: 360 });
    tabGliderSpringH = new AnalyticalSpring(h, val => {
      glider.style.height = `${Math.max(18, val).toFixed(1)}px`;
    }, { zeta: 0.82, omega0: 17, durationMs: 360 });
    updateTransform();
    glider.style.width = `${w}px`;
    glider.style.height = `${h}px`;
  } else {
    tabGliderSpringX.set(x);
    tabGliderSpringY.set(y);
    tabGliderSpringW.set(w);
    tabGliderSpringH.set(h);
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
      newlyWhitelisted.push({ id: c.id, title: stripLeadingEmoji(c.title), path: c.path || "", note });
    } else if (state.checked[c.id] && c.status !== "done" && !state.whitelisted[c.id]) {
      selected.push({ id: c.id, title: stripLeadingEmoji(c.title), path: c.path || "", note });
    }
  });

  const lines = [
    "请按我在全栈巡检看板上的勾选执行清理与优化（动手前请先用 `full_stack_tool.py preflight` 复查并发活跃状态）：",
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
      lines.push(`- \`${item.id}\`: ${item.path || item.title}${notePart}`);
    });
  }
  return lines.join(__JOIN_NEWLINE__);
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
    btn.textContent = "已复制到剪贴板";
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

initTheme();
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
    out = out.replace("__JOIN_NEWLINE__", '"\\n"')
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
