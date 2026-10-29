#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Design System & Visual Chart Architecture:
- Anthropic editorial aesthetic: warm obsidian dark default (#141413) & warm ivory paper light mode
  (#FAF9F5), hairline borders, terracotta (#D97757 / #C96442) & forest sage (#52A37A / #2E6B4E) accents.
- 3-Layer Interactive Storage Visualization (inspired by macOS Storage Settings, DaisyDisk, and
  Vercel Usage Breakdown):
  1. Whole-Disk Segmented Anatomy Bar + Directory/Category Breakdown Bars: shows Retained Used,
     Selected to Reclaim (subdivided per item), Unselected Candidates, and Free Space. Checking any
     item animates its exact slice out of the disk and out of its parent directory bar.
  2. Live "Selected Reclaim Composition" Proportional Strip + Spotlight Callout: visualizes every
     checked item (even a 3 GiB item) as a proportional block with relative impact metrics (e.g.,
     "+3.83 GiB · Boosts current free space by +10.8%").
  3. Per-Card Inline Visual Magnitude Bar & Bidirectional Hover Highlight: each candidate card has
     an inline spring-animated capacity bar and highlights its slice in the top charts on hover.
- Closed-form AnalyticalSpring (zeta = 0.72, ~3.8% non-linear light rebound) — 100% immune to Euler
  integration blowups.
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
    /* Anthropic Editorial Warm Paper Palette (Light Mode) */
    --bg-page: #FAF9F5;
    --bg-recessed: #F1EFE7;
    --bg-card: #FFFFFF;
    --bg-card-hover: #FCFBF8;
    --bg-card-selected: #FBF6F3;
    --bg-sticky: rgba(250, 249, 245, 0.93);
    --ink-primary: #141413;
    --ink-secondary: #52514C;
    --ink-muted: #87857E;
    --border-hairline: #E6E4DC;
    --border-strong: #CFCBC0;
    --accent-clay: #C96442;
    --accent-clay-soft: rgba(201, 100, 66, 0.14);
    --tone-sage: #2E6B4E;
    --tone-sage-bg: #EBF3EE;
    --tone-ochre: #B56A1E;
    --tone-ochre-bg: #F8EFE4;
    --tone-rust: #B83B2A;
    --tone-rust-bg: #FBECE9;
    --bar-retained: #4A4843;
    --bar-free: #E4E1D6;
    --shadow-subtle: 0 1px 2px rgba(20, 20, 19, 0.04);
    --shadow-modal: 0 24px 48px -12px rgba(20, 20, 19, 0.18);

    /* Typography */
    --font-serif: "Newsreader", "Instrument Serif", "Iowan Old Style", "Charter", "Songti SC", "STSong", Georgia, serif;
    --font-sans: -apple-system, BlinkMacSystemFont, "Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --font-mono: "SF Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

    /* Non-linear light rebound curves */
    --spring-rebound: cubic-bezier(0.28, 1.28, 0.56, 1);
    --spring-soft: cubic-bezier(0.22, 1.18, 0.38, 1);
  }

  [data-theme="dark"] {
    --bg-page: #141413;
    --bg-recessed: #1B1A18;
    --bg-card: #21201D;
    --bg-card-hover: #262521;
    --bg-card-selected: #27211E;
    --bg-sticky: rgba(20, 20, 19, 0.93);
    --ink-primary: #FAF9F5;
    --ink-secondary: #B5B2A8;
    --ink-muted: #7D7A72;
    --border-hairline: #2E2D29;
    --border-strong: #45433C;
    --accent-clay: #D97757;
    --accent-clay-soft: rgba(217, 119, 87, 0.16);
    --tone-sage: #52A37A;
    --tone-sage-bg: rgba(82, 163, 122, 0.15);
    --tone-ochre: #D99242;
    --tone-ochre-bg: rgba(217, 146, 66, 0.15);
    --tone-rust: #E06352;
    --tone-rust-bg: rgba(224, 99, 82, 0.15);
    --bar-retained: #45433D;
    --bar-free: #2A2925;
    --shadow-subtle: 0 1px 3px rgba(0, 0, 0, 0.28);
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
    transition: background-color 200ms ease, color 200ms ease;
  }
  .tabular {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  /* Top Sticky Command Bar */
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
    max-width: 1180px;
    margin: 0 auto;
    padding: 10px 20px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .brand-cluster {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .brand-mark {
    width: 9px;
    height: 9px;
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
    gap: 7px;
  }
  .summary-stat {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    font-size: 12px;
    color: var(--ink-secondary);
    transition: transform 260ms var(--spring-rebound);
  }
  .summary-stat.rebound {
    transform: scale(1.03);
  }
  .summary-stat strong {
    color: var(--ink-primary);
    font-weight: 600;
  }

  /* Buttons */
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
      border-color 150ms ease,
      background-color 150ms ease,
      color 150ms ease;
    user-select: none;
  }
  button:focus, button:focus-visible { outline: none; }
  @media (hover: hover) and (pointer: fine) {
    button:hover {
      border-color: var(--border-strong);
      background: var(--bg-card-hover);
    }
  }
  button:active {
    transform: scale(0.96);
    transition-duration: 55ms;
  }
  .btn-primary {
    background: var(--ink-primary);
    color: var(--bg-page);
    border-color: var(--ink-primary);
    font-weight: 600;
    padding: 5px 12px;
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

  /* Page Layout */
  .container {
    max-width: 1180px;
    margin: 0 auto;
    padding: 28px 20px 88px;
  }
  .editorial-header {
    margin-bottom: 24px;
    padding-bottom: 18px;
    border-bottom: 1px solid var(--border-hairline);
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
  }
  .editorial-kicker {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--accent-clay);
    margin-bottom: 6px;
  }
  h1 {
    font-family: var(--font-serif);
    font-size: clamp(24px, 2.8vw, 31px);
    font-weight: 600;
    letter-spacing: -0.015em;
    line-height: 1.2;
    margin: 0 0 6px;
    color: var(--ink-primary);
  }
  .editorial-subtitle {
    margin: 0;
    font-size: 13.5px;
    color: var(--ink-secondary);
    max-width: 680px;
  }

  .section-block {
    margin-bottom: 28px;
  }
  .section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
  }
  .section-title {
    font-family: var(--font-serif);
    font-size: 18px;
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

  /* ==========================================================================
     VISUALIZATION STUDIO: Interactive Disk Composition + Selected Reclaim Map
     ========================================================================== */
  .viz-studio {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 11px;
    padding: 18px;
    box-shadow: var(--shadow-subtle);
    margin-bottom: 14px;
  }
  .pool-selector-strip {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
    gap: 8px;
    margin-bottom: 16px;
  }
  .pool-tab-card {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
    transition:
      transform 240ms var(--spring-rebound),
      border-color 160ms ease,
      background-color 160ms ease;
  }
  .pool-tab-card:hover {
    border-color: var(--border-strong);
  }
  .pool-tab-card.active {
    background: var(--bg-card);
    border-color: var(--accent-clay);
    box-shadow: 0 0 0 1px var(--accent-clay);
  }
  .pool-tab-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-muted);
    white-space: nowrap;
  }
  .pool-tab-name {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pool-tab-metrics {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
    font-family: var(--font-mono);
    font-size: 12px;
    white-space: nowrap;
  }
  .pool-mini-track {
    position: relative;
    height: 5px;
    background: var(--bar-free);
    border-radius: 999px;
    overflow: hidden;
  }
  .pool-mini-ghost {
    position: absolute;
    inset: 0 auto 0 0;
    background: var(--border-strong);
    opacity: 0.45;
  }
  .pool-mini-fill {
    position: relative;
    height: 100%;
    border-radius: 999px;
    will-change: width;
  }

  /* Active Pool Deep-Dive Anatomy Chart */
  .anatomy-panel {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 15px 16px;
    margin-bottom: 14px;
  }
  .anatomy-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }
  .anatomy-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink-primary);
  }
  .anatomy-readout {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink-secondary);
  }

  /* 28px Segmented Storage Bar (macOS / DaisyDisk style) */
  .segmented-storage-bar {
    position: relative;
    height: 28px;
    width: 100%;
    background: var(--bar-free);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
    overflow: hidden;
    display: flex;
    margin-bottom: 10px;
  }
  .seg-retained {
    height: 100%;
    background: var(--bar-retained);
    display: flex;
    align-items: center;
    padding: 0 8px;
    color: #FAF9F5;
    font-family: var(--font-mono);
    font-size: 11px;
    white-space: nowrap;
    overflow: hidden;
    will-change: width;
  }
  .seg-reclaim-group {
    height: 100%;
    display: flex;
    overflow: hidden;
    will-change: width;
    background: repeating-linear-gradient(
      -45deg,
      rgba(82, 163, 122, 0.25),
      rgba(82, 163, 122, 0.25) 6px,
      rgba(82, 163, 122, 0.42) 6px,
      rgba(82, 163, 122, 0.42) 12px
    );
    border-left: 1.5px solid var(--tone-sage);
    border-right: 1.5px solid var(--tone-sage);
  }
  .seg-reclaim-slice {
    height: 100%;
    border-right: 1px solid rgba(20, 20, 19, 0.22);
    transition: opacity 150ms ease, filter 150ms ease;
    cursor: pointer;
    position: relative;
  }
  .seg-reclaim-slice:hover,
  .seg-reclaim-slice.highlighted {
    filter: brightness(1.22);
  }
  .seg-unselected {
    height: 100%;
    background: repeating-linear-gradient(
      45deg,
      rgba(217, 146, 66, 0.18),
      rgba(217, 146, 66, 0.18) 4px,
      transparent 4px,
      transparent 8px
    );
    border-right: 1px dashed var(--tone-ochre);
    will-change: width;
  }
  .seg-free {
    flex: 1;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 0 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    white-space: nowrap;
    overflow: hidden;
  }
  .storage-legend {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 11.5px;
    color: var(--ink-secondary);
  }
  .legend-items {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
  }
  .legend-dot {
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 2px;
    margin-right: 5px;
    vertical-align: -1px;
  }

  /* Category / Directory Breakdown Mini-Bars inside Active Pool */
  .category-breakdown-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 8px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border-hairline);
  }
  .cat-bar-card {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 8px 10px;
    min-width: 0;
  }
  .cat-bar-head {
    display: flex;
    justify-content: space-between;
    gap: 6px;
    font-size: 11.5px;
    margin-bottom: 5px;
  }
  .cat-bar-name {
    font-weight: 500;
    color: var(--ink-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .cat-bar-val {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-secondary);
    white-space: nowrap;
  }
  .cat-bar-track {
    height: 7px;
    background: var(--bg-recessed);
    border-radius: 4px;
    overflow: hidden;
    display: flex;
  }
  .cat-bar-remain {
    height: 100%;
    background: var(--bar-retained);
    transition: width 320ms var(--spring-soft);
  }
  .cat-bar-saved {
    height: 100%;
    background: var(--tone-sage);
    transition: width 320ms var(--spring-rebound);
  }

  /* Layer 2: Live Selected Reclaim Composition Treemap Strip & Spotlight */
  .reclaim-waterfall-panel {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 14px 16px;
  }
  .spotlight-banner {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 7px 11px;
    margin-bottom: 10px;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-left: 3px solid var(--accent-clay);
    border-radius: 6px;
    font-size: 12px;
    transition: transform 260ms var(--spring-rebound);
  }
  .spotlight-banner.bump {
    transform: scale(1.012);
  }
  .reclaim-strip {
    height: 22px;
    width: 100%;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    overflow: hidden;
    display: flex;
    margin-bottom: 10px;
  }
  .reclaim-chip-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .reclaim-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 8px;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 5px;
    font-size: 11.5px;
    cursor: pointer;
    transition: transform 200ms var(--spring-rebound), border-color 150ms ease;
  }
  .reclaim-chip:hover,
  .reclaim-chip.highlighted {
    border-color: var(--accent-clay);
    transform: translateY(-1px);
  }
  .chip-swatch {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    flex-shrink: 0;
  }

  /* Baseline Diff Ledger */
  .diff-card {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 14px 16px;
  }
  .diff-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    margin-top: 8px;
  }
  #diff-growth.diff-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  @media (max-width: 920px) {
    .diff-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 600px) {
    .diff-grid, #diff-growth.diff-grid { grid-template-columns: 1fr; }
  }
  .diff-item {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
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

  /* Segmented Filter Bar */
  .filter-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
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

  /* Candidate Cards with Inline Visual Magnitude Bar */
  .candidate-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  @keyframes editorialSlideIn {
    0% { opacity: 0; transform: translateY(6px); }
    70% { opacity: 1; transform: translateY(-1px); }
    100% { opacity: 1; transform: translateY(0); }
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
    animation: editorialSlideIn 260ms var(--spring-soft) both;
    transition:
      transform 220ms var(--spring-soft),
      border-color 150ms ease,
      background-color 180ms ease;
  }
  .candidate-card:hover,
  .candidate-card.highlighted {
    border-color: var(--border-strong);
  }
  .candidate-card.checked {
    background: var(--bg-card-selected);
    border-color: rgba(217, 119, 87, 0.42);
  }
  .candidate-card.pulse {
    transform: scale(1.006);
  }
  .candidate-card.done { opacity: 0.65; }
  .candidate-card.whitelisted { opacity: 0.75; border-style: dashed; }

  /* 15px Compact Checkbox */
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
    transition-duration: 50ms;
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

  .candidate-body { flex: 1; min-width: 0; }
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
  .status-pip {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;
    white-space: nowrap;
  }
  .pip-crit { background: var(--tone-rust-bg); color: var(--tone-rust); }
  .pip-warn { background: var(--tone-ochre-bg); color: var(--tone-ochre); }
  .pip-ok { background: var(--tone-sage-bg); color: var(--tone-sage); }

  .candidate-size {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
  }
  .candidate-title {
    font-weight: 600;
    font-size: 13.8px;
    color: var(--ink-primary);
    cursor: pointer;
    margin-bottom: 5px;
  }

  /* Inline Per-Card Visual Impact Meter (solves "I picked a 3G item, show me visually") */
  .card-impact-meter {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 5px 9px;
    margin-bottom: 6px;
  }
  .card-impact-track {
    flex: 1;
    height: 6px;
    background: var(--bar-free);
    border-radius: 999px;
    overflow: hidden;
    min-width: 60px;
  }
  .card-impact-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 340ms var(--spring-rebound), background-color 200ms ease;
  }
  .card-impact-label {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-secondary);
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
    font-size: 12.8px;
    color: var(--ink-secondary);
    margin-bottom: 9px;
  }
  .candidate-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .note-input {
    flex: 1;
    min-width: 200px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    color: var(--ink-primary);
    padding: 5px 10px;
    border-radius: 5px;
    font-family: var(--font-sans);
    font-size: 12px;
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
    background: rgba(20, 20, 19, 0.55);
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
  .modal-backdrop.open { opacity: 1; pointer-events: auto; }
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    max-width: 680px;
    width: 100%;
    padding: 22px;
    box-shadow: var(--shadow-modal);
    transform: scale(0.95) translateY(10px);
    transition: transform 280ms var(--spring-rebound);
  }
  .modal-backdrop.open .modal { transform: scale(1) translateY(0); }
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
      <span class="brand-meta">FULL-STACK MAINTENANCE · STORAGE VISUALIZER</span>
    </div>
    <div class="action-cluster">
      <div class="summary-stat tabular" id="sticky-summary">
        <span>已选 <strong id="selected-count">0</strong> 项</span>
        <span style="color:var(--border-strong)">|</span>
        <span>释放 <strong id="selected-gib" style="color:var(--tone-sage)">0.00 GiB</strong></span>
      </div>
      <button onclick="selectByTier(['A'])">全选 A 级</button>
      <button onclick="selectByTier(['A', 'B'])">选 A+B</button>
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
      <div class="editorial-kicker">STORAGE ANATOMY &amp; RECLAIM SIMULATION</div>
      <h1>__HEADING__</h1>
      <p class="editorial-subtitle">
        记录时间：__GENERATED_AT__。点击或勾选任意清理项，上方「存储池分段解剖图」与「本次释放构成图」将按欠阻尼弹簧物理实时演进，直观呈现每一项对磁盘与可用空间的实际贡献。
      </p>
    </div>
    <div>
      <button class="btn-ghost" onclick="resetSavedState()">重置缓存状态</button>
    </div>
  </div>

  <!-- ==================== 3-LAYER INTERACTIVE STORAGE VISUALIZATION ==================== -->
  <section class="section-block">
    <div class="section-header">
      <h2 class="section-title">存储空间解剖与实时清理可视化</h2>
      <span class="section-caption">点击存储池卡片切换解剖视图；勾选下方项目实时观察色块切分与释放比例</span>
    </div>

    <div class="viz-studio">
      <!-- 5-Pool Selector Strip -->
      <div class="pool-selector-strip" id="pool-selector-strip"></div>

      <!-- Layer 1: Active Pool Segmented Storage Bar + Directory Breakdown -->
      <div class="anatomy-panel" id="anatomy-panel">
        <div class="anatomy-head">
          <span class="anatomy-title" id="anatomy-pool-title">存储池空间构成解剖</span>
          <span class="anatomy-readout tabular" id="anatomy-pool-readout"></span>
        </div>
        <div class="segmented-storage-bar" id="segmented-storage-bar">
          <div class="seg-retained" id="seg-retained" style="width:50%"></div>
          <div class="seg-reclaim-group" id="seg-reclaim-group" style="width:0%"></div>
          <div class="seg-unselected" id="seg-unselected" style="width:0%"></div>
          <div class="seg-free" id="seg-free"></div>
        </div>
        <div class="storage-legend">
          <div class="legend-items">
            <span><i class="legend-dot" style="background:var(--bar-retained)"></i>清理后保留已用 <strong class="mono" id="leg-retained">0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--tone-sage)"></i>已勾选待释放 <strong class="mono" style="color:var(--tone-sage)" id="leg-reclaim">+0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--tone-ochre)"></i>未勾选候选 <strong class="mono" id="leg-unselected">0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--bar-free);border:1px solid var(--border-strong)"></i>原有可用 <strong class="mono" id="leg-free">0 GiB</strong></span>
          </div>
          <span class="mono" style="color:var(--tone-sage);font-weight:600" id="leg-total-free-after"></span>
        </div>

        <!-- Directory / Category Breakdown Mini-Charts -->
        <div class="category-breakdown-grid" id="category-breakdown-grid"></div>
      </div>

      <!-- Layer 2: Selected Reclaim Proportional Waterfall & Last-Toggled Spotlight -->
      <div class="reclaim-waterfall-panel">
        <div class="spotlight-banner" id="spotlight-banner">
          <span id="spotlight-text">勾选下方任意项目（例如 3 GiB 的编译缓存），此处将实时拆解该项目占本次释放总量的比例与可用空间增幅。</span>
          <span class="mono" style="color:var(--accent-clay);font-weight:600" id="spotlight-badge">实时联动</span>
        </div>
        <div class="anatomy-head" style="margin-bottom:6px">
          <span class="anatomy-title" style="font-size:12.5px">本次已勾选项目 · 空间释放构成比例图（悬停色块或卡片可双向高亮）</span>
          <span class="anatomy-readout tabular" id="waterfall-total-label">共 0 项 · 0.00 GiB</span>
        </div>
        <div class="reclaim-strip" id="reclaim-strip"></div>
        <div class="reclaim-chip-list" id="reclaim-chip-list"></div>
      </div>
    </div>
  </section>

  <!-- Baseline Diff Section -->
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

  <!-- Layer 3: Actionable Checklist with Inline Per-Card Visual Magnitude Bars -->
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

// Warm Anthropic-inspired categorical palette for proportional chart slices
const SLICE_PALETTE = [
  "#D97757", "#52A37A", "#D99242", "#6E98B8", "#B87D95",
  "#8CA878", "#C96442", "#4B8F8C", "#C48F45", "#7B88A8"
];

/**
 * Closed-form Analytical Underdamped Spring Evaluator (zeta = 0.72 -> ~3.8% light rebound).
 */
class AnalyticalSpring {
  constructor(initialValue, onUpdate, config = {}) {
    this.current = initialValue;
    this.startVal = initialValue;
    this.target = initialValue;
    this.zeta = config.zeta || 0.72;
    this.omega0 = config.omega0 || 15.0;
    this.omegaD = this.omega0 * Math.sqrt(1 - this.zeta * this.zeta);
    this.durationMs = config.durationMs || 440;
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
let activePoolId = ((REPORT_DATA.storage_gauges || [])[0] || {}).id || "mac";
let state = loadState();
const gaugeSprings = {};
let anatomyRetainedSpring = null;
let anatomyReclaimSpring = null;
let anatomyUnselectedSpring = null;
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
    else if (c.default_checked != null ? c.default_checked : c.tier === "A") initialChecked[c.id] = true;
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

function getColorForIndex(idx) {
  return SLICE_PALETTE[idx % SLICE_PALETTE.length];
}

function initGaugesDOM() {
  const strip = document.getElementById("pool-selector-strip");
  strip.innerHTML = "";
  (REPORT_DATA.storage_gauges || []).forEach((g, idx) => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const warnPct = Number(g.warn_used_pct || g.warn_pct) || 85;
    const cleanLabel = stripLeadingEmoji(g.label || g.id);
    const indexStr = String(idx + 1).padStart(2, "0");

    const card = document.createElement("div");
    card.id = "pool-tab-" + g.id;
    card.className = "pool-tab-card" + (g.id === activePoolId ? " active" : "");
    card.onclick = () => selectPool(g.id);
    card.innerHTML = `
      <div class="pool-tab-top">
        <span>POOL ${indexStr} · ${formatCompactBytes(total)}</span>
        <span class="status-pip" id="gpip-${escapeHtml(g.id)}"></span>
      </div>
      <div class="pool-tab-name" title="${escapeHtml(cleanLabel)}">${escapeHtml(cleanLabel)}</div>
      <div class="pool-tab-metrics tabular">
        <span><s style="color:var(--ink-muted);font-size:10.5px;margin-right:3px" id="gbefore-${escapeHtml(g.id)}">${curPct.toFixed(1)}%</s><strong id="gpct-${escapeHtml(g.id)}">${curPct.toFixed(1)}%</strong></span>
        <span style="color:var(--tone-sage);font-size:11px;font-weight:600" id="gsaved-${escapeHtml(g.id)}">+0 GiB</span>
      </div>
      <div class="pool-mini-track">
        <div class="pool-mini-ghost" style="width:${Math.min(100, curPct).toFixed(1)}%"></div>
        <div class="pool-mini-fill" id="gfill-${escapeHtml(g.id)}" style="width:${Math.min(100, curPct).toFixed(1)}%"></div>
      </div>
    `;
    strip.appendChild(card);

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
        pipEl.textContent = isCrit ? "告警" : (isWarn ? "偏高" : "健康");
      }
    });
  });

  anatomyRetainedSpring = new AnalyticalSpring(50, val => {
    const el = document.getElementById("seg-retained");
    if (el) el.style.width = Math.max(0, Math.min(100, val)).toFixed(2) + "%";
  });
  anatomyReclaimSpring = new AnalyticalSpring(0, val => {
    const el = document.getElementById("seg-reclaim-group");
    if (el) el.style.width = Math.max(0, Math.min(100, val)).toFixed(2) + "%";
  });
  anatomyUnselectedSpring = new AnalyticalSpring(0, val => {
    const el = document.getElementById("seg-unselected");
    if (el) el.style.width = Math.max(0, Math.min(100, val)).toFixed(2) + "%";
  });

  totalSavingsSpring = new AnalyticalSpring(0, val => {
    const safeGiB = Math.max(0, val);
    const dockEl = document.getElementById("selected-gib");
    if (dockEl) dockEl.textContent = safeGiB.toFixed(2) + " GiB";
  });
}

function selectPool(poolId) {
  activePoolId = poolId;
  document.querySelectorAll(".pool-tab-card").forEach(el => {
    el.classList.toggle("active", el.id === "pool-tab-" + poolId);
  });
  updateVisualCharts();
}

function inferCategoryName(c) {
  if (c.category) return c.category;
  const p = String(c.path || "");
  if (p.includes("Mobile Documents") || p.includes("CloudDocs")) return "~/Library (iCloud 与容器)";
  if (p.includes("CoreSimulator") || p.includes("Library/Caches")) return "~/Library (模拟器与缓存)";
  if (p.includes("/var/tmp") || p.includes("/private/var")) return "/private/var (系统临时区)";
  if (p.includes("~/Codex") || p.includes("worktrees")) return "~/Codex (本地项目与构建)";
  if (p.includes("/System/Volumes/Data") || p.includes("macOS Install")) return "系统卷与安装器残留";
  if (p.includes("90_归档") || p.includes("#recycle")) return "NAS 归档与回收站";
  if (p.includes("Resolve-Work") || p.includes("PVE-Backups")) return "NVMe 协作缓存与备份";
  if (p.includes("docker") || p.includes("local-lvm") || p.includes("/boot/efi")) return "PVE 宿主机与容器镜像";
  return "其他可回收项";
}

function updateVisualCharts(lastToggledItem = null) {
  const savingsByGauge = {};
  const unselectedByGauge = {};
  let totalSavingsBytes = 0;
  const checkedCandidates = [];

  (REPORT_DATA.candidates || []).forEach((c, idx) => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    const sz = Number(c.size_bytes) || 0;
    const gid = c.gauge_id || "";
    if (state.checked[c.id]) {
      totalSavingsBytes += sz;
      if (gid) savingsByGauge[gid] = (savingsByGauge[gid] || 0) + sz;
      if (sz > 0) checkedCandidates.push({ ...c, _color: getColorForIndex(idx) });
    } else {
      if (gid && sz > 0) unselectedByGauge[gid] = (unselectedByGauge[gid] || 0) + sz;
    }
  });

  if (totalSavingsSpring) totalSavingsSpring.set(totalSavingsBytes / (1024 ** 3));
  pulseElement(document.getElementById("sticky-summary"), "rebound", 220);

  // Update 5 pool mini-cards
  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    const saved = savingsByGauge[g.id] || 0;
    const newUsed = Math.max(0, used - saved);
    const curPct = total > 0 ? (used / total) * 100 : (Number(g.used_pct) || 0);
    const newPct = total > 0 ? (newUsed / total) * 100 : curPct;

    if (gaugeSprings[g.id]) gaugeSprings[g.id].set(newPct);
    const beforeEl = document.getElementById("gbefore-" + g.id);
    if (beforeEl) beforeEl.style.display = saved > 0 ? "inline" : "none";
    const savedEl = document.getElementById("gsaved-" + g.id);
    if (savedEl) {
      savedEl.textContent = saved > 0 ? ("-" + formatCompactBytes(saved)) : "无变动";
      savedEl.style.color = saved > 0 ? "var(--tone-sage)" : "var(--ink-muted)";
    }
  });

  // Layer 1: Active Pool Segmented Anatomy Bar
  const gauges = REPORT_DATA.storage_gauges || [];
  const activePool = gauges.find(g => g.id === activePoolId) || gauges[0];
  if (activePool) {
    const total = Math.max(1, Number(activePool.total_bytes) || 1);
    const used = Number(activePool.used_bytes) || 0;
    const free = activePool.free_bytes != null ? Number(activePool.free_bytes) : Math.max(0, total - used);
    const saved = Math.min(used, savingsByGauge[activePool.id] || 0);
    const unsel = Math.min(Math.max(0, used - saved), unselectedByGauge[activePool.id] || 0);
    const retained = Math.max(0, used - saved - unsel);

    const retainedPct = (retained / total) * 100;
    const reclaimPct = (saved / total) * 100;
    const unselPct = (unsel / total) * 100;

    if (anatomyRetainedSpring) anatomyRetainedSpring.set(retainedPct);
    if (anatomyReclaimSpring) anatomyReclaimSpring.set(reclaimPct);
    if (anatomyUnselectedSpring) anatomyUnselectedSpring.set(unselPct);

    const cleanPoolName = stripLeadingEmoji(activePool.label || activePool.id);
    document.getElementById("anatomy-pool-title").textContent = `${cleanPoolName} · 空间构成解剖图`;
    document.getElementById("anatomy-pool-readout").textContent =
      `总容量 ${formatBytes(total)} ｜ 已用 ${(used / total * 100).toFixed(1)}% → 模拟清理后 ${((used - saved) / total * 100).toFixed(1)}%`;

    const retEl = document.getElementById("seg-retained");
    if (retEl) retEl.textContent = retainedPct > 16 ? `保留已用 ${formatCompactBytes(retained)}` : "";
    const freePct = Math.max(0, 100 - retainedPct - reclaimPct - unselPct);
    const freeEl = document.getElementById("seg-free");
    if (freeEl) freeEl.textContent = freePct > 18 ? `可用 ${formatCompactBytes(free + saved)}` : "";

    document.getElementById("leg-retained").textContent = formatCompactBytes(retained);
    document.getElementById("leg-reclaim").textContent = "+" + formatCompactBytes(saved);
    document.getElementById("leg-unselected").textContent = formatCompactBytes(unsel);
    document.getElementById("leg-free").textContent = formatCompactBytes(free);
    document.getElementById("leg-total-free-after").textContent =
      `模拟后总可用：${formatCompactBytes(free)} + ${formatCompactBytes(saved)} = ${formatCompactBytes(free + saved)}`;

    // Sub-slices inside seg-reclaim-group for activePool
    const reclaimGroup = document.getElementById("seg-reclaim-group");
    reclaimGroup.innerHTML = "";
    const poolChecked = checkedCandidates.filter(c => c.gauge_id === activePool.id);
    poolChecked.forEach(c => {
      const slice = document.createElement("div");
      slice.className = "seg-reclaim-slice";
      slice.dataset.itemId = c.id;
      const wPct = saved > 0 ? (Number(c.size_bytes) / saved) * 100 : 0;
      slice.style.width = `${wPct.toFixed(2)}%`;
      slice.style.backgroundColor = c._color;
      slice.title = `${c.id}: ${stripLeadingEmoji(c.title)} (${formatBytes(c.size_bytes)})`;
      slice.onmouseenter = () => highlightItem(c.id, true);
      slice.onmouseleave = () => highlightItem(c.id, false);
      slice.onclick = () => scrollToCard(c.id);
      reclaimGroup.appendChild(slice);
    });

    // Directory / Category Breakdown Mini-Charts for activePool
    renderPoolCategoryBreakdown(activePool);
  }

  // Layer 2: Live Selected Reclaim Waterfall Strip & Chips
  renderReclaimWaterfall(checkedCandidates, totalSavingsBytes, lastToggledItem);
}

function renderPoolCategoryBreakdown(pool) {
  const box = document.getElementById("category-breakdown-grid");
  box.innerHTML = "";
  const poolItems = (REPORT_DATA.candidates || []).filter(c => c.gauge_id === pool.id && Number(c.size_bytes) > 0);
  const baseCategories = Array.isArray(pool.breakdown) ? pool.breakdown : null;

  const catMap = {};
  if (baseCategories) {
    baseCategories.forEach(cat => {
      catMap[cat.name] = { name: cat.name, total: Number(cat.size_bytes) || 0, saved: 0 };
    });
  }
  poolItems.forEach(c => {
    const catName = inferCategoryName(c);
    if (!catMap[catName]) {
      catMap[catName] = { name: catName, total: 0, saved: 0 };
    }
    const sz = Number(c.size_bytes) || 0;
    if (!baseCategories) catMap[catName].total += sz;
    if (state.checked[c.id] && !state.whitelisted[c.id] && c.status !== "done") {
      catMap[catName].saved += sz;
    }
  });

  const rows = Object.values(catMap).filter(r => r.total > 0);
  const maxCatTotal = Math.max(1, ...rows.map(r => r.total));

  rows.forEach(r => {
    const saved = Math.min(r.total, r.saved);
    const remain = Math.max(0, r.total - saved);
    const remainPct = (remain / maxCatTotal) * 100;
    const savedPct = (saved / maxCatTotal) * 100;

    const div = document.createElement("div");
    div.className = "cat-bar-card tabular";
    div.innerHTML = `
      <div class="cat-bar-head">
        <span class="cat-bar-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
        <span class="cat-bar-val">
          ${saved > 0 ? `<strong style="color:var(--tone-sage)">-${formatCompactBytes(saved)}</strong> / ` : ""}${formatCompactBytes(r.total)}
        </span>
      </div>
      <div class="cat-bar-track">
        <div class="cat-bar-remain" style="width:${remainPct.toFixed(1)}%"></div>
        <div class="cat-bar-saved" style="width:${savedPct.toFixed(1)}%" title="本次勾选释放 ${formatBytes(saved)}"></div>
      </div>
    `;
    box.appendChild(div);
  });
}

function renderReclaimWaterfall(checkedCandidates, totalSavingsBytes, lastToggledItem) {
  const strip = document.getElementById("reclaim-strip");
  const chips = document.getElementById("reclaim-chip-list");
  const totalLabel = document.getElementById("waterfall-total-label");
  strip.innerHTML = "";
  chips.innerHTML = "";

  const sorted = [...checkedCandidates].sort((a, b) => (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0));
  totalLabel.textContent = `已选 ${sorted.length} 个空间项 · 合计释放 ${formatBytes(totalSavingsBytes)}`;

  if (sorted.length === 0 || totalSavingsBytes <= 0) {
    strip.innerHTML = `<div style="padding:0 10px;display:flex;align-items:center;font-size:11.5px;color:var(--ink-muted)">尚未勾选带空间释放的条目</div>`;
  } else {
    sorted.forEach(c => {
      const sz = Number(c.size_bytes) || 0;
      const pct = (sz / totalSavingsBytes) * 100;
      const seg = document.createElement("div");
      seg.className = "seg-reclaim-slice";
      seg.dataset.itemId = c.id;
      seg.style.width = `${Math.max(1.2, pct).toFixed(2)}%`;
      seg.style.backgroundColor = c._color;
      seg.title = `${c.id} · ${stripLeadingEmoji(c.title)}: ${formatBytes(sz)} (占释放总量 ${pct.toFixed(1)}%)`;
      seg.onmouseenter = () => highlightItem(c.id, true);
      seg.onmouseleave = () => highlightItem(c.id, false);
      seg.onclick = () => scrollToCard(c.id);
      strip.appendChild(seg);

      const chip = document.createElement("div");
      chip.className = "reclaim-chip tabular";
      chip.dataset.itemId = c.id;
      chip.onmouseenter = () => highlightItem(c.id, true);
      chip.onmouseleave = () => highlightItem(c.id, false);
      chip.onclick = () => scrollToCard(c.id);
      chip.innerHTML = `
        <span class="chip-swatch" style="background:${c._color}"></span>
        <span class="mono" style="color:var(--ink-muted)">${escapeHtml(c.id)}</span>
        <strong class="mono">${formatCompactBytes(sz)}</strong>
        <span class="mono" style="color:var(--ink-muted);font-size:10.5px">(${pct.toFixed(1)}%)</span>
      `;
      chips.appendChild(chip);
    });
  }

  // Update Spotlight Banner when user toggles any item (even a 3 GiB item!)
  if (lastToggledItem) {
    const banner = document.getElementById("spotlight-banner");
    const textEl = document.getElementById("spotlight-text");
    const badgeEl = document.getElementById("spotlight-badge");
    const isNowChecked = !!state.checked[lastToggledItem.id];
    const sz = Number(lastToggledItem.size_bytes) || 0;
    const pool = (REPORT_DATA.storage_gauges || []).find(g => g.id === lastToggledItem.gauge_id);
    const poolFree = pool ? (pool.free_bytes != null ? Number(pool.free_bytes) : Math.max(0, Number(pool.total_bytes) - Number(pool.used_bytes))) : 0;
    const boostPct = poolFree > 0 ? ((sz / poolFree) * 100).toFixed(1) : "0.0";

    if (sz > 0) {
      textEl.innerHTML = isNowChecked
        ? `已勾选 <strong>${escapeHtml(lastToggledItem.id)} · ${escapeHtml(stripLeadingEmoji(lastToggledItem.title))}</strong>：释放 <strong style="color:var(--tone-sage)">+${formatBytes(sz)}</strong>${pool ? `（相当于 ${escapeHtml(stripLeadingEmoji(pool.label))} 当前剩余可用空间的 <strong>+${boostPct}%</strong>）` : ""}`
        : `已取消勾选 <strong>${escapeHtml(lastToggledItem.id)}</strong>：少释放 <strong>${formatBytes(sz)}</strong>`;
      badgeEl.textContent = isNowChecked ? `+${formatCompactBytes(sz)}` : `-${formatCompactBytes(sz)}`;
    } else {
      textEl.innerHTML = `${isNowChecked ? "已勾选" : "已取消"}配置优化项 <strong>${escapeHtml(lastToggledItem.id)} · ${escapeHtml(stripLeadingEmoji(lastToggledItem.title))}</strong>`;
      badgeEl.textContent = isNowChecked ? "已纳入执行" : "已移除";
    }
    pulseElement(banner, "bump", 240);
  }
}

function highlightItem(itemId, active) {
  document.querySelectorAll(`[data-item-id="${itemId}"]`).forEach(el => {
    el.classList.toggle("highlighted", active);
  });
  const card = document.getElementById("card-" + itemId);
  if (card) card.classList.toggle("highlighted", active);
}

function scrollToCard(itemId) {
  const card = document.getElementById("card-" + itemId);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    pulseElement(card, "pulse", 320);
  }
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

function updateSelectionTotals(lastToggledItem = null) {
  let checkedCount = 0;
  (REPORT_DATA.candidates || []).forEach(c => {
    if (!state.whitelisted[c.id] && c.status !== "done" && state.checked[c.id]) {
      checkedCount++;
    }
  });
  const countEl = document.getElementById("selected-count");
  if (countEl) countEl.textContent = String(checkedCount);
  updateTabCounts();
  updateVisualCharts(lastToggledItem);
}

function renderCandidates() {
  const list = document.getElementById("candidate-list");
  list.innerHTML = "";
  let visibleIndex = 0;

  // Compute pool-level total reclaimable bytes so each card can render a meaningful visual impact bar
  const poolReclaimableMax = {};
  const poolFreeMap = {};
  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Number(g.total_bytes) || 0;
    const used = Number(g.used_bytes) || 0;
    poolFreeMap[g.id] = g.free_bytes != null ? Number(g.free_bytes) : Math.max(0, total - used);
  });
  (REPORT_DATA.candidates || []).forEach(c => {
    const gid = c.gauge_id || "_default";
    const sz = Number(c.size_bytes) || 0;
    poolReclaimableMax[gid] = (poolReclaimableMax[gid] || 0) + sz;
  });

  (REPORT_DATA.candidates || []).forEach((c, idx) => {
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
    const sz = Number(c.size_bytes) || 0;
    const gid = c.gauge_id || "_default";
    const poolTotalReclaim = Math.max(1, poolReclaimableMax[gid] || 1);
    const shareOfPoolReclaimPct = (sz / poolTotalReclaim) * 100;
    // Perceptual sqrt curve so a 3 GiB item has a distinct, visible bar (~28% width) vs a 47 GiB item
    const visualBarPct = sz > 0 ? Math.min(100, Math.max(8, Math.sqrt(sz / poolTotalReclaim) * 100)) : 0;
    const baseFree = poolFreeMap[c.gauge_id] || 0;
    const freeBoostPct = (sz > 0 && baseFree > 0) ? ((sz / baseFree) * 100).toFixed(1) : null;
    const sliceColor = getColorForIndex(idx);

    const card = document.createElement("div");
    card.id = "card-" + c.id;
    card.className = "candidate-card" + (isChecked ? " checked" : "") + (isDone ? " done" : "") + (isWl ? " whitelisted" : "");
    card.style.animationDelay = `${Math.min(visibleIndex * 20, 160)}ms`;
    card.onmouseenter = () => highlightItem(c.id, true);
    card.onmouseleave = () => highlightItem(c.id, false);
    visibleIndex++;

    const impactBarHtml = sz > 0 ? `
      <div class="card-impact-meter tabular">
        <div class="card-impact-track">
          <div class="card-impact-fill" id="cimp-${escapeHtml(c.id)}"
            style="width:${isChecked ? visualBarPct.toFixed(1) : Math.max(4, visualBarPct * 0.35).toFixed(1)}%;background:${isChecked ? sliceColor : 'var(--border-strong)'}"></div>
        </div>
        <span class="card-impact-label">
          占本池可清理量 <strong>${shareOfPoolReclaimPct.toFixed(1)}%</strong>${freeBoostPct ? ` · 可用空间增幅 <strong style="color:var(--tone-sage)">+${freeBoostPct}%</strong>` : ""}
        </span>
      </div>
    ` : "";

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
          <span class="candidate-size tabular">${formatBytes(sz)}</span>
        </div>
        <div class="candidate-title" onclick="clickCardTitle('${escapeHtml(c.id)}')">${escapeHtml(stripLeadingEmoji(c.title))}</div>
        ${impactBarHtml}
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
  const cIdx = (REPORT_DATA.candidates || []).findIndex(x => x.id === id);
  const c = cIdx >= 0 ? REPORT_DATA.candidates[cIdx] : null;
  const card = document.getElementById("card-" + id);
  if (card) {
    card.classList.toggle("checked", !!checked);
    pulseElement(card, "pulse", 200);
  }
  // Animate the inline card impact meter bar with spring rebound
  if (c && Number(c.size_bytes) > 0) {
    const impEl = document.getElementById("cimp-" + id);
    if (impEl) {
      const poolTotal = (REPORT_DATA.candidates || [])
        .filter(x => (x.gauge_id || "_default") === (c.gauge_id || "_default"))
        .reduce((acc, x) => acc + (Number(x.size_bytes) || 0), 0) || 1;
      const visualBarPct = Math.min(100, Math.max(8, Math.sqrt(Number(c.size_bytes) / poolTotal) * 100));
      impEl.style.width = (checked ? visualBarPct : Math.max(4, visualBarPct * 0.35)).toFixed(1) + "%";
      impEl.style.backgroundColor = checked ? getColorForIndex(cIdx) : "var(--border-strong)";
    }
    if (c.gauge_id && c.gauge_id !== activePoolId) {
      activePoolId = c.gauge_id;
      document.querySelectorAll(".pool-tab-card").forEach(el => {
        el.classList.toggle("active", el.id === "pool-tab-" + activePoolId);
      });
    }
  }
  updateSelectionTotals(c);
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
