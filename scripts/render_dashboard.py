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
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    white-space: nowrap;
    overflow: hidden;
  }
  .pool-mini-track {
    position: relative;
    height: 6px;
    background: var(--bar-free);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
    align-items: center;
  }
  .pool-mini-fill {
    height: 100%;
    transition: width 320ms var(--spring-soft), background-color 180ms ease;
    will-change: width;
  }
  .pool-mini-focus {
    height: 100%;
    background: var(--accent-clay);
    transition: width 320ms var(--spring-rebound);
  }
  .pool-mini-reclaim {
    height: 100%;
    background: var(--tone-sage);
    transition: width 320ms var(--spring-rebound);
  }
  .pool-reclaim-pill {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    color: var(--tone-sage);
    margin-left: auto;
    margin-right: 4px;
  }

  /* Active Pool Deep-Dive Anatomy Chart: Left = Total Capacity, Middle = Progress Bar (adjusting Cleaned Capacity in middle), Right = Used Capacity */
  .anatomy-panel {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 15px 16px;
    margin-bottom: 12px;
  }
  .anatomy-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }
  .anatomy-title-group {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .anatomy-title {
    font-size: 13.8px;
    font-weight: 600;
    color: var(--ink-primary);
  }
  .anatomy-focus-badge {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--accent-clay);
    background: var(--accent-clay-soft);
    padding: 2px 8px;
    border-radius: 999px;
  }
  .anatomy-mid-badge {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--tone-sage);
    background: var(--tone-sage-bg);
    padding: 2px 8px;
    border-radius: 999px;
  }
  .anatomy-readout {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--ink-secondary);
  }
  .main-storage-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
  }
  .main-storage-total {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-secondary);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .main-storage-used {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink-secondary);
    white-space: nowrap;
    flex-shrink: 0;
  }

  /* 28px Clean Solid Storage Bar: [Used After Clean (Left)] [Current Item (Terracotta)] [Other Cleaned (Sage)] [Free Space (Right)] */
  .segmented-storage-bar {
    position: relative;
    height: 28px;
    flex: 1;
    min-width: 120px;
    background: var(--bar-free);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
    overflow: hidden;
    display: flex;
    align-items: stretch;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.12);
  }
  .seg-retained {
    height: 100%;
    background: var(--bar-retained);
    display: flex;
    align-items: center;
    padding: 0 10px;
    color: #FAF9F5;
    font-family: var(--font-mono);
    font-size: 11px;
    white-space: nowrap;
    overflow: hidden;
    transition: width 320ms var(--spring-soft);
    will-change: width;
  }
  .seg-reclaim-item {
    height: 100%;
    background: var(--accent-clay);
    border-right: 1.5px solid rgba(20, 20, 19, 0.38);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #FAF9F5;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    transition: width 320ms var(--spring-rebound);
  }
  .seg-reclaim-mid {
    height: 100%;
    background: var(--tone-sage);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    color: #FAF9F5;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    transition: width 320ms var(--spring-rebound);
    will-change: width;
  }
  .seg-free {
    flex: 1;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 0 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    white-space: nowrap;
    overflow: hidden;
  }
  @media (max-width: 760px) {
    .seg-retained, .seg-reclaim-item, .seg-reclaim-mid, .seg-free { font-size: 0 !important; padding: 0 !important; }
  }
  .storage-legend {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 6px 12px;
    font-size: 11px;
    color: var(--ink-secondary);
  }
  .legend-items {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 13px;
  }
  .legend-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 2px;
    margin-right: 4px;
    vertical-align: -0.5px;
  }
  .legend-free-pill {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-secondary);
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    padding: 1.5px 8px;
    border-radius: 5px;
    white-space: nowrap;
  }

  /* Category / Directory Breakdown Mini-Bars: Left = Total, Middle = [Retained][Current Item (Terracotta)][Other Cleaned (Sage)], Right = Used */
  .category-breakdown-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
    gap: 8px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border-hairline);
  }
  .cat-bar-card {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 9px 11px;
    min-width: 0;
    transition: border-color 160ms ease, background-color 160ms ease;
  }
  .cat-bar-card.focused-cat {
    border-color: rgba(217, 119, 87, 0.5);
  }
  .cat-bar-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    margin-bottom: 5px;
  }
  .cat-bar-name {
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .cat-bar-clean-tag {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    color: var(--tone-sage);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .cat-bar-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: var(--font-mono);
    font-size: 10.5px;
  }
  .cat-bar-total {
    color: var(--ink-muted);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .cat-bar-val {
    color: var(--ink-secondary);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .cat-bar-track {
    flex: 1;
    height: 8px;
    background: var(--bar-free);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
    align-items: stretch;
  }
  .cat-bar-remain {
    height: 100%;
    background: var(--bar-retained);
    transition: width 320ms var(--spring-soft);
  }
  .cat-bar-focus {
    height: 100%;
    background: var(--accent-clay);
    transition: width 320ms var(--spring-rebound);
  }
  .cat-bar-saved {
    height: 100%;
    background: var(--tone-sage);
    transition: width 320ms var(--spring-rebound);
  }

  /* Live Action Spotlight Banner */
  .spotlight-banner {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 12px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-left: 3px solid var(--tone-sage);
    border-radius: 7px;
    font-size: 12px;
    transition: transform 260ms var(--spring-rebound);
  }
  .spotlight-banner.bump {
    transform: scale(1.01);
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

  /* Two-Level Segmented Filter Bar (Domain Row + Action Category Row) */
  .filter-stack {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 14px;
  }
  .filter-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .filter-subrow {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .sub-pills {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 2px;
    padding: 3px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
  }
  .sub-pills button {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border: 1px solid transparent;
    background: transparent;
    box-shadow: none;
    padding: 4px 10px;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-secondary);
    outline: none;
    transition:
      background-color 160ms ease,
      border-color 160ms ease,
      color 160ms ease,
      transform 220ms var(--spring-rebound);
  }
  .sub-pills button:hover {
    color: var(--ink-primary);
  }
  .sub-pills button.active {
    background: var(--bg-card);
    border-color: var(--border-hairline);
    color: var(--ink-primary);
    font-weight: 600;
    box-shadow: var(--shadow-subtle);
  }
  .sub-dot {
    width: 6.5px;
    height: 6.5px;
    border-radius: 50%;
    display: inline-block;
  }
  .btn-sub-select {
    font-size: 11.5px;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    color: var(--ink-secondary);
  }
  .btn-sub-select:hover {
    color: var(--ink-primary);
    border-color: var(--border-strong);
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
    display: none;
  }
  .segmented-control button {
    position: relative;
    z-index: 1;
    border: 1px solid transparent;
    background: transparent;
    box-shadow: none;
    padding: 4px 10px;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-secondary);
    outline: none;
    transition:
      background-color 160ms ease,
      border-color 160ms ease,
      color 160ms ease,
      transform 220ms var(--spring-rebound);
  }
  .segmented-control button.active {
    background: var(--bg-card);
    border-color: var(--border-hairline);
    color: var(--ink-primary);
    font-weight: 600;
    box-shadow: var(--shadow-subtle);
  }
  .tab-count {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-muted);
    margin-left: 3px;
  }

  .sort-control {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 3px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 7px;
  }
  .sort-control button {
    border: none;
    background: transparent;
    box-shadow: none;
    padding: 4px 9px;
    border-radius: 5px;
    font-size: 11.5px;
    color: var(--ink-secondary);
  }
  .sort-control button.active {
    background: var(--bg-card);
    color: var(--ink-primary);
    font-weight: 600;
    box-shadow: var(--shadow-subtle);
  }

  /* Candidate Cards with Inline Visual Magnitude Bar */
  .candidate-list {
    display: flex;
    flex-direction: column;
    gap: 9px;
  }
  @keyframes editorialSlideIn {
    0% { transform: translateY(3px); }
    100% { transform: translateY(0); }
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
    animation: editorialSlideIn 220ms var(--spring-soft);
    transition:
      transform 220ms var(--spring-soft),
      border-color 150ms ease,
      background-color 180ms ease,
      box-shadow 180ms ease;
  }
  .candidate-card:hover,
  .candidate-card.highlighted {
    border-color: var(--border-strong);
  }
  .candidate-card.checked {
    background: var(--bg-card-selected);
    border-color: rgba(217, 119, 87, 0.28);
    box-shadow: inset 3px 0 0 var(--accent-clay), var(--shadow-subtle);
  }
  .candidate-card.checked:hover,
  .candidate-card.checked.highlighted {
    border-color: rgba(217, 119, 87, 0.55);
  }
  .candidate-card.pulse {
    transform: scale(1.006);
  }
  .candidate-card.done { opacity: 0.65; }
  .candidate-card.whitelisted { opacity: 0.78; }

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
    gap: 6px;
    margin-bottom: 4px;
  }
  .item-id {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-muted);
  }
  .pool-badge {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-secondary);
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    padding: 0.5px 6px;
    border-radius: 4px;
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
  .action-tag {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;
    border: 1px solid var(--border-hairline);
  }
  .action-update { background: rgba(110, 152, 184, 0.16); color: #6E98B8; border-color: rgba(110, 152, 184, 0.32); }
  .action-optimize { background: var(--tone-sage-bg); color: var(--tone-sage); border-color: rgba(82, 163, 122, 0.3); }
  .action-organize { background: var(--tone-ochre-bg); color: var(--tone-ochre); border-color: rgba(217, 146, 66, 0.3); }
  .action-cleanup { background: var(--bg-recessed); color: var(--ink-secondary); }
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
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
  }
  .candidate-title {
    font-weight: 600;
    font-size: 14px;
    color: var(--ink-primary);
    cursor: pointer;
    margin-bottom: 6px;
  }

  /* Inline Per-Card Storage Bar:
     Row 1: [Left: Total Capacity] [Middle: ● Current Item Cleanup (Terracotta) · ● Total Pool Cleanup (Sage)] [Right: Used Capacity]
     Row 2: Unobstructed Progress Bar [Retained Used (Gray)] [Current Item (Terracotta)] [Other Pool Cleanup (Sage)] [Free Space] */
  .card-impact-meter {
    display: flex;
    flex-direction: column;
    gap: 5px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 6px;
    padding: 7px 10px;
    margin-bottom: 7px;
  }
  .card-impact-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }
  .card-impact-total {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .card-impact-mid-tag {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
  }
  .card-impact-label {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-secondary);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .card-impact-track {
    position: relative;
    width: 100%;
    height: 11px;
    background: var(--bar-free);
    border: 1px solid var(--border-hairline);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
    align-items: stretch;
  }
  .card-impact-retained {
    height: 100%;
    background: var(--bar-retained);
    transition: width 340ms var(--spring-soft);
  }
  .card-impact-preview {
    height: 100%;
    background: var(--accent-clay);
    opacity: 0.28;
    transition: width 340ms var(--spring-rebound);
  }
  .card-impact-reclaim {
    height: 100%;
    background: var(--accent-clay);
    border-right: 1.5px solid var(--bg-recessed);
    transition: width 340ms var(--spring-rebound);
  }
  .card-impact-other {
    height: 100%;
    background: var(--tone-sage);
    transition: width 340ms var(--spring-soft);
  }
  .dot-item {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 2px;
    background: var(--accent-clay);
    flex-shrink: 0;
  }
  .dot-item-ghost {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 2px;
    border: 1.5px solid var(--accent-clay);
    background: transparent;
    flex-shrink: 0;
  }
  .dot-pool {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 2px;
    background: var(--tone-sage);
    flex-shrink: 0;
  }

  .candidate-meta-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 8px;
  }
  .candidate-path {
    display: inline-block;
    align-self: flex-start;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    padding: 1.5px 7px;
    border-radius: 4px;
    word-break: break-all;
  }
  .candidate-evidence {
    font-size: 12.6px;
    color: var(--ink-secondary);
    line-height: 1.48;
  }
  .candidate-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    align-items: center;
  }
  .note-input {
    flex: 1;
    min-width: 200px;
    height: 28px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    color: var(--ink-primary);
    padding: 3px 9px;
    border-radius: 5px;
    font-family: var(--font-sans);
    font-size: 11.5px;
  }
  .note-input:focus {
    outline: none;
    background: var(--bg-card);
    border-color: var(--accent-clay);
  }
  .btn-whitelist {
    height: 28px;
    padding: 0 10px;
    font-size: 11.5px;
    background: transparent;
    box-shadow: none;
    color: var(--ink-muted);
  }
  .btn-whitelist:hover {
    color: var(--ink-primary);
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

  /* ==========================================================================
     FIRST-SCREEN GIANT STORAGE RING HERO & DETAILED BREAKDOWN
     ========================================================================== */
  .view-mode-switch {
    display: inline-flex;
    padding: 2px;
    background: var(--bg-recessed);
    border: 1px solid var(--border-strong);
    border-radius: 7px;
    gap: 2px;
  }
  .view-mode-switch button {
    border: none;
    background: transparent;
    box-shadow: none;
    padding: 4px 11px;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--ink-secondary);
    transition: background-color 160ms ease, color 160ms ease, transform 180ms var(--spring-rebound);
  }
  .view-mode-switch button.active {
    background: var(--accent-clay);
    color: #FAF9F5;
    font-weight: 600;
    box-shadow: var(--shadow-subtle);
  }
  .top-ring-summary {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 11px;
    border-radius: 999px;
    background: var(--tone-sage-bg);
    border: 1px solid rgba(82, 163, 122, 0.32);
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--tone-sage);
  }
  .workbench-only-cluster {
    display: none;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
  }
  .header-kpi-pills {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .header-kpi-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 11px;
    border-radius: 7px;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    font-size: 12px;
    color: var(--ink-secondary);
    box-shadow: var(--shadow-subtle);
  }
  .header-kpi-pill strong {
    font-family: var(--font-mono);
    color: var(--ink-primary);
    font-weight: 600;
  }

  .ring-hero-card {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 14px;
    padding: 26px 28px;
    box-shadow: var(--shadow-subtle);
    margin-bottom: 22px;
  }
  .ring-hero-layout {
    display: grid;
    grid-template-columns: 332px minmax(0, 1fr);
    gap: 32px;
    align-items: stretch;
  }
  @media (max-width: 920px) {
    .ring-hero-layout {
      grid-template-columns: 1fr;
      justify-items: center;
    }
  }
  .giant-ring-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-between;
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 12px;
    padding: 22px 18px 16px;
    width: 100%;
  }
  .giant-ring-stage {
    position: relative;
    width: 288px;
    height: 288px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .giant-ring-svg {
    width: 288px;
    height: 288px;
    transform: rotate(-90deg);
    overflow: visible;
  }
  .ring-seg {
    fill: none;
    stroke-width: 24;
    stroke-linecap: butt;
  }
  .ring-seg-cleaned {
    filter: drop-shadow(0 0 10px rgba(82, 163, 122, 0.32));
  }
  .ring-seg-focus {
    filter: drop-shadow(0 0 12px rgba(217, 119, 87, 0.48));
  }
  .giant-ring-center {
    position: absolute;
    inset: 38px;
    border-radius: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 6px;
    pointer-events: none;
    gap: 5px;
  }
  .ring-center-kicker {
    font-family: var(--font-sans);
    font-size: 11.5px;
    font-weight: 500;
    letter-spacing: 0.06em;
    color: var(--ink-secondary);
    white-space: nowrap;
  }
  .ring-center-val-wrap {
    display: inline-flex;
    align-items: baseline;
    justify-content: center;
    gap: 4px;
    white-space: nowrap;
    margin: 1px 0 2px;
  }
  .ring-center-num {
    font-family: var(--font-sans);
    font-size: 38px;
    font-weight: 700;
    line-height: 1.02;
    letter-spacing: -0.03em;
    color: var(--tone-sage);
  }
  .ring-center-unit {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 600;
    color: var(--tone-sage);
    opacity: 0.9;
  }
  .ring-center-water {
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    padding: 2.5px 10px;
    border-radius: 999px;
    background: var(--tone-sage-bg);
    border: 1px solid rgba(82, 163, 122, 0.28);
    color: var(--tone-sage);
    white-space: nowrap;
  }
  .ring-center-sub {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-muted);
    white-space: nowrap;
    margin-top: 1px;
  }
  .ring-legend-bar {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    padding-top: 14px;
    margin-top: 12px;
    border-top: 1px solid var(--border-hairline);
  }
  .ring-legend-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    text-align: center;
  }
  .ring-legend-label {
    font-size: 11px;
    color: var(--ink-secondary);
    display: inline-flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }
  .ring-legend-val {
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
  }

  .ring-right-panel {
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 16px;
    min-width: 0;
    width: 100%;
  }
  .ring-headline-box {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-hairline);
  }
  .ring-headline-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .ring-title-cluster {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
  }
  .ring-pool-title {
    font-family: var(--font-serif);
    font-size: 22px;
    font-weight: 600;
    color: var(--ink-primary);
    margin: 0;
    line-height: 1.2;
  }
  .ring-pool-mount {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--ink-muted);
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    padding: 2px 8px;
    border-radius: 5px;
  }
  .ring-headline-badge {
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--tone-sage);
    background: var(--tone-sage-bg);
    border: 1px solid rgba(82, 163, 122, 0.3);
    padding: 3px 10px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .ring-pool-desc {
    font-size: 12.8px;
    color: var(--ink-secondary);
    margin: 0;
  }

  .ring-kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
  }
  @media (max-width: 720px) {
    .ring-kpi-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .ring-kpi-card {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 5px;
    min-width: 0;
  }
  .ring-kpi-card.highlight-cleaned {
    background: var(--tone-sage-bg);
    border-color: rgba(82, 163, 122, 0.38);
  }
  .ring-kpi-label {
    font-size: 11.5px;
    color: var(--ink-secondary);
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .ring-kpi-val {
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 700;
    color: var(--ink-primary);
    line-height: 1.2;
    white-space: nowrap;
  }
  .ring-kpi-card.highlight-cleaned .ring-kpi-val {
    color: var(--tone-sage);
  }
  .ring-kpi-sub {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mini-matrix-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .mini-matrix-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .mini-matrix-title {
    font-family: var(--font-mono);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--ink-muted);
  }
  .mini-rings-strip {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 9px;
  }
  @media (max-width: 720px) {
    .mini-rings-strip {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .mini-ring-card {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 9px 11px;
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    min-width: 0;
    transition:
      border-color 160ms ease,
      transform 220ms var(--spring-rebound),
      background-color 160ms ease,
      box-shadow 160ms ease;
  }
  .mini-ring-card:hover {
    border-color: var(--border-strong);
    background: var(--bg-card-hover);
    transform: translateY(-1px);
  }
  .mini-ring-card:active {
    transform: scale(0.98);
  }
  .mini-ring-card.active {
    background: var(--bg-card);
    border-color: var(--tone-sage);
    box-shadow: 0 0 0 1px var(--tone-sage);
  }
  .mini-ring-wrap {
    position: relative;
    width: 38px;
    height: 38px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .mini-ring-svg {
    width: 38px;
    height: 38px;
    transform: rotate(-90deg);
  }
  .mini-ring-pct {
    position: absolute;
    font-family: var(--font-mono);
    font-size: 9.5px;
    font-weight: 600;
    color: var(--ink-secondary);
    pointer-events: none;
  }
  .mini-ring-info {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    flex: 1;
  }
  .mini-ring-row1 {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
    min-width: 0;
  }
  .mini-ring-name {
    font-size: 12.2px;
    font-weight: 600;
    color: var(--ink-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .mini-ring-saved {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--tone-sage);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .mini-ring-saved.neutral {
    color: var(--ink-secondary);
    font-weight: 500;
  }
  .mini-ring-meta {
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: var(--ink-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Detailed Breakdown Below Ring — Strict 3-Column Table Grid */
  .detail-group-block {
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 12px;
    padding: 18px 22px;
    margin-bottom: 18px;
    box-shadow: var(--shadow-subtle);
  }
  .detail-group-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    padding-bottom: 12px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border-hairline);
  }
  .detail-group-title {
    font-family: var(--font-serif);
    font-size: 17px;
    font-weight: 600;
    color: var(--ink-primary);
    margin: 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .detail-rows {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .detail-row {
    background: var(--bg-recessed);
    border: 1px solid var(--border-hairline);
    border-radius: 9px;
    padding: 13px 16px;
    display: grid;
    grid-template-columns: 36px minmax(0, 1fr) 186px;
    gap: 14px;
    align-items: flex-start;
    transition:
      border-color 150ms ease,
      background-color 150ms ease,
      transform 180ms var(--spring-soft),
      box-shadow 150ms ease;
  }
  @media (max-width: 720px) {
    .detail-row {
      grid-template-columns: 32px minmax(0, 1fr);
    }
    .drow-right {
      grid-column: 2;
      text-align: left !important;
      align-items: flex-start !important;
    }
  }
  .detail-row:hover {
    border-color: var(--accent-clay);
    background: var(--bg-card-hover);
    box-shadow: inset 3px 0 0 var(--accent-clay);
  }
  .drow-idx {
    width: 30px;
    height: 24px;
    margin-top: 1px;
    border-radius: 6px;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    color: var(--ink-muted);
    flex-shrink: 0;
    transition: color 150ms ease, border-color 150ms ease;
  }
  .detail-row:hover .drow-idx {
    color: var(--accent-clay);
    border-color: var(--accent-clay);
  }
  .drow-main {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .drow-title-line {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .drow-title-text {
    font-weight: 600;
    font-size: 14px;
    color: var(--ink-primary);
    line-height: 1.35;
  }
  .drow-meta-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .drow-sub-line {
    font-size: 12.3px;
    color: var(--ink-secondary);
    line-height: 1.48;
  }
  .drow-path {
    display: inline-block;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    padding: 1px 7px;
    border-radius: 4px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .drow-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    justify-content: flex-start;
    gap: 5px;
    text-align: right;
    padding-top: 1px;
  }
  .drow-amount {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 700;
    color: var(--tone-sage);
    white-space: nowrap;
    line-height: 1.2;
    transition: color 150ms ease;
  }
  .detail-row:hover .drow-amount.is-space {
    color: var(--accent-clay);
  }
  .drow-share {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-muted);
    white-space: nowrap;
  }
  .drow-bar-track {
    width: 136px;
    height: 5px;
    background: var(--bar-free);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
    justify-content: flex-end;
  }
  .drow-bar-fill {
    height: 100%;
    background: var(--tone-sage);
    border-radius: 999px;
    transition: width 320ms var(--spring-soft), background-color 150ms ease;
  }
  .detail-row:hover .drow-bar-fill {
    background: var(--accent-clay);
  }
</style>
</head>
<body>

<header class="top-bar">
  <div class="top-bar-inner">
    <div class="brand-cluster">
      <span class="brand-mark"></span>
      <span class="brand-meta">FULL-STACK MAINTENANCE · STORAGE VISUALIZER</span>
      <span class="top-ring-summary tabular" id="top-ring-summary">✨ 全栈已清理释放</span>
    </div>
    <div class="action-cluster">
      <div class="workbench-only-cluster" id="workbench-action-cluster">
        <div class="summary-stat tabular" id="sticky-summary">
          <span>已选 <strong id="selected-count">0</strong> 项</span>
          <span style="color:var(--border-strong)">|</span>
          <span>释放 <strong id="selected-gib" style="color:var(--tone-sage)">0.00 GiB</strong></span>
        </div>
        <button onclick="selectByTier(['A'])">全选 A 级</button>
        <button onclick="selectByTier(['A', 'B'])">选 A+B</button>
        <button onclick="selectOptimizations()">选优化/整理/更新</button>
        <button onclick="clearSelections()">清空</button>
        <button class="btn-ghost" onclick="resetSavedState()">重置</button>
        <button class="btn-primary" onclick="copyExecutionPrompt()">导出执行指令</button>
      </div>
      <div class="view-mode-switch" role="tablist" aria-label="页面视图切换">
        <button type="button" id="view-btn-ring" class="active" onclick="switchMainView('ring')">🎯 圆环总览与明细</button>
        <button type="button" id="view-btn-workbench" onclick="switchMainView('workbench')">🎛️ 勾选与微调工作台</button>
      </div>
      <div class="theme-switch" role="group" aria-label="外观模式切换">
        <button type="button" id="theme-dark-btn" class="active" onclick="setTheme('dark')">深色</button>
        <button type="button" id="theme-light-btn" onclick="setTheme('light')">浅色</button>
      </div>
    </div>
  </div>
</header>

<div class="container">
  <div class="editorial-header">
    <div>
      <div class="editorial-kicker">STORAGE RING OVERVIEW &amp; CLEANUP REPORT</div>
      <h1>__HEADING__</h1>
      <p class="editorial-subtitle">
        巡检快照时间：__GENERATED_AT__ · 首屏圆环直观展示硬盘总容量与<strong>本次清掉的空间（翡翠绿高亮扇区）</strong>，下方按存储卷与类别列出全部空间清理、系统优化与白名单保护明细。
      </p>
    </div>
    <div class="header-kpi-pills tabular" id="header-kpi-pills"></div>
  </div>

  <!-- ==================== PAGE 1 (DEFAULT): GIANT STORAGE RING & DETAILED LIST ==================== -->
  <div id="ring-overview-page">
    <section class="ring-hero-card">
      <div class="ring-hero-layout">
        <!-- Left Column: Precision Giant Donut Ring + Aligned 3-Segment Legend -->
        <div class="giant-ring-col">
          <div class="giant-ring-stage">
            <svg class="giant-ring-svg" viewBox="0 0 288 288" aria-label="磁盘容量与本次清理比例圆环图">
              <!-- Subtle outer precision scale ring -->
              <circle cx="144" cy="144" r="135" fill="none" stroke="var(--border-hairline)" stroke-width="1" stroke-dasharray="2 5"></circle>
              <!-- Base track: 100% capacity (uncovered part = Original Free Space) -->
              <circle cx="144" cy="144" r="116" fill="none" stroke="var(--bar-free)" stroke-width="24"></circle>
              <!-- Segment 1: Retained Used Space after cleanup -->
              <circle id="ring-arc-retained" class="ring-seg" cx="144" cy="144" r="116"
                stroke="var(--bar-retained)" stroke-dasharray="0 728.85" stroke-dashoffset="0"></circle>
              <!-- Segment 2: Cleaned / Reclaimed Space (Distinct Emerald Sage Color!) -->
              <circle id="ring-arc-cleaned" class="ring-seg ring-seg-cleaned" cx="144" cy="144" r="116"
                stroke="var(--tone-sage)" stroke-dasharray="0 728.85" stroke-dashoffset="0"></circle>
              <!-- Segment 3: Hovered Focus Item Slice (Terracotta Orange) -->
              <circle id="ring-arc-focus" class="ring-seg ring-seg-focus" cx="144" cy="144" r="116"
                stroke="var(--accent-clay)" stroke-dasharray="0 728.85" stroke-dashoffset="0"></circle>
            </svg>
            <div class="giant-ring-center tabular">
              <span class="ring-center-kicker" id="ring-center-kicker">本次已清掉空间</span>
              <div class="ring-center-val-wrap">
                <span class="ring-center-num" id="ring-center-num">-0.00</span>
                <span class="ring-center-unit" id="ring-center-unit">GiB</span>
              </div>
              <div class="ring-center-water" id="ring-center-water">水位 0% → 0%</div>
              <div class="ring-center-sub" id="ring-center-sub">可用 0 GiB / 总 0 GiB</div>
            </div>
          </div>

          <div class="ring-legend-bar tabular">
            <div class="ring-legend-item">
              <span class="ring-legend-label"><i class="legend-dot" style="background:var(--bar-retained)"></i>清理后已用</span>
              <span class="ring-legend-val" id="rleg-retained">0 GiB</span>
            </div>
            <div class="ring-legend-item">
              <span class="ring-legend-label"><i class="legend-dot" style="background:var(--tone-sage)"></i>本次清掉</span>
              <span class="ring-legend-val" style="color:var(--tone-sage)" id="rleg-cleaned">-0 GiB</span>
            </div>
            <div class="ring-legend-item">
              <span class="ring-legend-label"><i class="legend-dot" style="background:var(--bar-free);border:1px solid var(--border-strong)"></i>清理后可用</span>
              <span class="ring-legend-val" id="rleg-free">0 GiB</span>
            </div>
          </div>
        </div>

        <!-- Right side of Giant Ring: Active Volume Header + 4 KPI Cards + 3x3 Storage Pool Matrix -->
        <div class="ring-right-panel">
          <div class="ring-headline-box">
            <div class="ring-headline-top">
              <div class="ring-title-cluster">
                <h2 class="ring-pool-title" id="ring-active-title">Mac 本机磁盘</h2>
                <span class="ring-pool-mount" id="ring-active-mount">/System/Volumes/Data</span>
              </div>
              <span class="ring-headline-badge tabular" id="ring-active-badge">释放 0.0% 容量</span>
            </div>
            <p class="ring-pool-desc" id="ring-active-desc">
              圆环整圈代表当前存储卷 100% 总容量：深灰段为清理后保留的已用空间，<strong>翡翠绿高亮扇区为本次清掉释放的空间</strong>；悬停下方任意明细行可在圆环上以陶土橙高亮该项占比。
            </p>
          </div>

          <div class="ring-kpi-grid tabular">
            <div class="ring-kpi-card">
              <span class="ring-kpi-label"><i class="legend-dot" style="background:var(--border-strong)"></i>硬盘总容量 (100%)</span>
              <span class="ring-kpi-val" id="rkpi-total">0 GiB</span>
              <span class="ring-kpi-sub" id="rkpi-before-used">清理前已用 0 GiB</span>
            </div>
            <div class="ring-kpi-card highlight-cleaned">
              <span class="ring-kpi-label"><i class="legend-dot" style="background:var(--tone-sage)"></i>✨ 本次清掉空间</span>
              <span class="ring-kpi-val" id="rkpi-cleaned">-0 GiB</span>
              <span class="ring-kpi-sub" id="rkpi-cleaned-pct">占总容量 0.0%</span>
            </div>
            <div class="ring-kpi-card">
              <span class="ring-kpi-label"><i class="legend-dot" style="background:var(--bar-retained)"></i>清理后已用空间</span>
              <span class="ring-kpi-val" id="rkpi-retained">0 GiB</span>
              <span class="ring-kpi-sub" id="rkpi-retained-pct">现水位 0.0%</span>
            </div>
            <div class="ring-kpi-card">
              <span class="ring-kpi-label"><i class="legend-dot" style="background:var(--bar-free);border:1px solid var(--border-strong)"></i>清理后总可用</span>
              <span class="ring-kpi-val" id="rkpi-free-after" style="color:var(--tone-sage)">0 GiB</span>
              <span class="ring-kpi-sub" id="rkpi-free-before">清理前可用 0 GiB</span>
            </div>
          </div>

          <div class="mini-matrix-section">
            <div class="mini-matrix-head">
              <span class="mini-matrix-title">全栈存储卷切换矩阵（点击切换左侧主圆环与下方明细）</span>
              <span class="section-caption" id="mini-matrix-hint">共 9 个视图</span>
            </div>
            <div class="mini-rings-strip" id="mini-rings-strip"></div>
          </div>
        </div>
      </div>
    </section>

    <!-- Detailed List Directly Below the Giant Ring -->
    <div id="ring-detailed-lists"></div>
  </div>

  <!-- ==================== PAGE 2: INTERACTIVE BAR & CHECKBOX WORKBENCH ==================== -->
  <div id="workbench-page" style="display:none">
  <section class="section-block">
    <div class="section-header">
      <h2 class="section-title">存储空间总容量与已用容量实时视图</h2>
      <span class="section-caption" id="studio-summary-caption">双色清理标尺：陶土橙 ＝ 当前焦点项 ｜ 鼠尾草绿 ＝ 本池其他已选汇总</span>
    </div>

    <div class="viz-studio">
      <!-- 5-Pool Selector Strip -->
      <div class="pool-selector-strip" id="pool-selector-strip"></div>

      <!-- Active Pool Bar: Left = Total Capacity, Middle = Progress Bar (Cleaned in Middle), Right = Used Capacity -->
      <div class="anatomy-panel" id="anatomy-panel">
        <div class="anatomy-head">
          <div class="anatomy-title-group">
            <span class="anatomy-title" id="anatomy-pool-title">Mac 内置 SSD (APFS)</span>
            <span class="anatomy-focus-badge tabular" id="anatomy-focus-badge" style="display:none">当前项目 -0 GiB</span>
            <span class="anatomy-mid-badge tabular" id="anatomy-mid-badge">总清理 -0.00 GiB</span>
          </div>
          <span class="anatomy-readout tabular" id="anatomy-pool-sub">原已用 0 GiB → 清理后已用 0 GiB</span>
        </div>
        <div class="main-storage-row tabular">
          <span class="main-storage-total" id="main-storage-total">总容量 0 GiB</span>
          <div class="segmented-storage-bar" id="segmented-storage-bar">
            <div class="seg-retained" id="seg-retained" style="width:48%"></div>
            <div class="seg-reclaim-item" id="seg-reclaim-item" style="width:7%"></div>
            <div class="seg-reclaim-mid" id="seg-reclaim-mid" style="width:15%"></div>
            <div class="seg-free" id="seg-free"></div>
          </div>
          <span class="main-storage-used" id="anatomy-pool-readout">已用 0 GiB</span>
        </div>
        <div class="storage-legend">
          <div class="legend-items">
            <span><i class="legend-dot" style="background:var(--bar-retained)"></i>清理后已用 <strong class="mono" id="leg-retained">0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--accent-clay)"></i>当前项目 <strong class="mono" style="color:var(--accent-clay)" id="leg-focus-item">0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--tone-sage)"></i>本池总清理 <strong class="mono" style="color:var(--tone-sage)" id="leg-reclaim">0 GiB</strong></span>
            <span><i class="legend-dot" style="background:var(--bar-free);border:1px solid var(--border-strong)"></i>原本空闲 <strong class="mono" id="leg-free">0 GiB</strong></span>
          </div>
          <span class="legend-free-pill" id="leg-total-free-after"></span>
        </div>

        <!-- Directory / Category Breakdown Mini-Charts -->
        <div class="category-breakdown-grid" id="category-breakdown-grid"></div>
      </div>

      <!-- Live Action Spotlight Banner -->
      <div class="spotlight-banner" id="spotlight-banner">
        <span id="spotlight-text">悬停或切换下方任意项目，上方主存储池、目录分项与卡片内嵌条将同步以「陶土橙（本项）＋ 鼠尾草绿（其他已选）」联动。</span>
        <span class="mono" style="color:var(--tone-sage);font-weight:600" id="spotlight-badge">实时联动</span>
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

    <div class="filter-stack">
      <!-- Level 1: Primary Domain Menu + Sort Control -->
      <div class="filter-bar">
        <div class="segmented-control" id="domain-tabs" role="tablist" aria-label="一级巡检领域">
          <div class="tab-glider" id="tab-glider"></div>
          <button class="active" data-domain="all" onclick="setDomain('all', this)">全部领域 <span class="tab-count" id="cnt-all"></span></button>
          <button data-domain="mac" onclick="setDomain('mac', this)">Mac 与备份 <span class="tab-count" id="cnt-mac"></span></button>
          <button data-domain="projects" onclick="setDomain('projects', this)">本地项目 <span class="tab-count" id="cnt-projects"></span></button>
          <button data-domain="github" onclick="setDomain('github', this)">GitHub <span class="tab-count" id="cnt-github"></span></button>
          <button data-domain="nas" onclick="setDomain('nas', this)">NAS 与 PVE <span class="tab-count" id="cnt-nas"></span></button>
          <button data-domain="whitelisted" onclick="setDomain('whitelisted', this)">白名单 <span class="tab-count" id="cnt-whitelisted"></span></button>
        </div>
        <div class="sort-control" role="group" aria-label="排序方式">
          <button type="button" class="active" data-sort="default" onclick="setSortMode('default', this)">默认顺序</button>
          <button type="button" data-sort="opt" onclick="setSortMode('opt', this)">优化/更新优先</button>
          <button type="button" data-sort="size" onclick="setSortMode('size', this)">按释放空间</button>
          <button type="button" data-sort="pool" onclick="setSortMode('pool', this)">按存储池</button>
        </div>
      </div>

      <!-- Level 2: Clean Secondary Action Category Row -->
      <div class="filter-subrow" id="action-subbar">
        <div class="sub-pills" id="action-tabs" role="tablist" aria-label="动作分类">
          <button class="active" data-action="all" onclick="setActionFilter('all', this)">
            全部 <span class="tab-count" id="acnt-all"></span>
          </button>
          <button data-action="cleanup" onclick="setActionFilter('cleanup', this)">
            <i class="sub-dot" style="background:var(--accent-clay)"></i>空间清理 <span class="tab-count" id="acnt-cleanup"></span>
          </button>
          <button data-action="organize" onclick="setActionFilter('organize', this)">
            <i class="sub-dot" style="background:var(--tone-ochre)"></i>结构整理 <span class="tab-count" id="acnt-organize"></span>
          </button>
          <button data-action="optimize" onclick="setActionFilter('optimize', this)">
            <i class="sub-dot" style="background:var(--tone-sage)"></i>性能优化 <span class="tab-count" id="acnt-optimize"></span>
          </button>
          <button data-action="update" onclick="setActionFilter('update', this)">
            <i class="sub-dot" style="background:#6E98B8"></i>版本更新 <span class="tab-count" id="acnt-update"></span>
          </button>
        </div>
        <button type="button" class="btn-sub-select" onclick="selectVisibleCategory()">全选当前列表</button>
      </div>
    </div>

    <main class="candidate-list" id="candidate-list"></main>
  </section>
  </div>
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
const REDUCED_MOTION = Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

// Warm Anthropic-inspired categorical palette for proportional chart slices
const SLICE_PALETTE = [
  "#D97757", "#52A37A", "#D99242", "#6E98B8", "#B87D95",
  "#8CA878", "#C96442", "#4B8F8C", "#C48F45", "#7B88A8"
];

// Safe storage helpers: sandboxed iframes (without allow-same-origin) throw SecurityError on window.localStorage access
function safeStorageGet(key) {
  try {
    return window.localStorage ? window.localStorage.getItem(key) : null;
  } catch (e) {
    return null;
  }
}

function safeStorageSet(key, val) {
  try {
    if (window.localStorage) window.localStorage.setItem(key, val);
  } catch (e) {}
}

function safeStorageRemove(key) {
  try {
    if (window.localStorage) window.localStorage.removeItem(key);
  } catch (e) {}
}

let currentDomain = "all";
let currentAction = "all";
let currentSort = "default";
let activePoolId = ((REPORT_DATA.storage_gauges || [])[0] || {}).id || "mac";
let state = loadState();

function initTheme() {
  const saved = safeStorageGet(THEME_KEY);
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
  safeStorageSet(THEME_KEY, theme);
  applyTheme(theme);
}

function loadState() {
  try {
    const raw = safeStorageGet(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        return {
          checked: parsed.checked || {},
          notes: parsed.notes || {},
          whitelisted: parsed.whitelisted || {}
        };
      }
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
  safeStorageSet(STORAGE_KEY, JSON.stringify(state));
}

function stripLeadingEmoji(str) {
  return String(str || "").replace(/^[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}\\u{FE0F}\\s]+/u, "").trim();
}

function shortPoolName(poolId) {
  const g = (REPORT_DATA.storage_gauges || []).find(x => x.id === poolId);
  if (!g) return "";
  const raw = stripLeadingEmoji(g.label || g.id);
  return raw.replace(/\\s*\\(.*?\\)\\s*/g, "").trim();
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
  if (abs >= 1024 ** 4) return (b / (1024 ** 4)).toFixed(2) + " TiB";
  if (abs >= 1024 ** 3) return (b / (1024 ** 3)).toFixed(2) + " GiB";
  if (abs >= 1024 ** 2) return (b / (1024 ** 2)).toFixed(1) + " MiB";
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

function updatePoolMiniBarState(poolId, pctVal, warnPct) {
  const clamped = Math.max(0, Math.min(100, pctVal));
  const fillEl = document.getElementById("gfill-" + poolId);
  const pipEl = document.getElementById("gpip-" + poolId);
  const isCrit = clamped >= warnPct;
  const isWarn = !isCrit && clamped >= warnPct - 10;

  if (fillEl) {
    fillEl.style.width = clamped.toFixed(2) + "%";
    fillEl.style.backgroundColor = isCrit ? "var(--tone-rust)" : (isWarn ? "var(--tone-ochre)" : "var(--bar-retained)");
  }
  if (pipEl) {
    pipEl.className = "status-pip " + (isCrit ? "pip-crit" : (isWarn ? "pip-warn" : "pip-ok"));
    pipEl.textContent = isCrit ? "告警" : (isWarn ? "偏高" : "健康");
  }
}

function initGaugesDOM() {
  const strip = document.getElementById("pool-selector-strip");
  if (!strip) return;
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
        <span>POOL ${indexStr}</span>
        <span class="pool-reclaim-pill tabular" id="grecpill-${escapeHtml(g.id)}"></span>
        <span class="status-pip" id="gpip-${escapeHtml(g.id)}"></span>
      </div>
      <div class="pool-tab-name" title="${escapeHtml(cleanLabel)}">${escapeHtml(cleanLabel)}</div>
      <div class="pool-tab-metrics tabular">
        <span style="color:var(--ink-muted)">总 ${formatCompactBytes(total)}</span>
        <span style="color:var(--ink-secondary)">已用 <strong style="color:var(--ink-primary)" id="gused-${escapeHtml(g.id)}">${formatCompactBytes(used)}</strong></span>
      </div>
      <div class="pool-mini-track">
        <div class="pool-mini-fill" id="gfill-${escapeHtml(g.id)}" style="width:${Math.min(100, curPct).toFixed(1)}%"></div>
        <div class="pool-mini-focus" id="gfocus-${escapeHtml(g.id)}" style="width:0%"></div>
        <div class="pool-mini-reclaim" id="greclaim-${escapeHtml(g.id)}" style="width:0%"></div>
      </div>
    `;
    strip.appendChild(card);
    updatePoolMiniBarState(g.id, curPct, warnPct);
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

function getCardPoolMetrics(c) {
  const pool = (REPORT_DATA.storage_gauges || []).find(g => g.id === c.gauge_id);
  if (pool && Number(pool.total_bytes) > 0) {
    return {
      totalBytes: Number(pool.total_bytes),
      origUsedBytes: Number(pool.used_bytes) || 0,
      poolId: pool.id
    };
  }
  const fallbackTotal = (REPORT_DATA.candidates || [])
    .filter(x => (x.gauge_id || "_default") === (c.gauge_id || "_default"))
    .reduce((acc, x) => acc + (Number(x.size_bytes) || 0), 0) || 1;
  return { totalBytes: fallbackTotal, origUsedBytes: fallbackTotal, poolId: "_default" };
}

function updateAllCardBars(savingsByGauge) {
  (REPORT_DATA.candidates || []).forEach(c => {
    const sz = Number(c.size_bytes) || 0;
    if (sz <= 0) return;
    const isWl = !!state.whitelisted[c.id];
    const isDone = c.status === "done";
    const isChecked = !isWl && !isDone && !!state.checked[c.id];

    const { totalBytes, origUsedBytes, poolId } = getCardPoolMetrics(c);
    const poolSaved = Math.min(origUsedBytes, savingsByGauge[poolId] || 0);
    const usedAfterPool = Math.max(0, origUsedBytes - poolSaved);

    // Exact byte-accurate proportions relative to totalBytes (100% = totalBytes):
    const thisItemSaved = isChecked ? Math.min(poolSaved, sz) : 0;
    const otherPoolSaved = Math.max(0, poolSaved - thisItemSaved);
    const previewBytes = (!isChecked && !isWl && !isDone) ? Math.min(usedAfterPool, sz) : 0;
    const retainedBytes = Math.max(0, usedAfterPool - previewBytes);

    const retainedPct = (retainedBytes / totalBytes) * 100;
    const previewPct = (previewBytes / totalBytes) * 100;
    const otherSavedPct = (otherPoolSaved / totalBytes) * 100;
    const thisItemPct = (thisItemSaved / totalBytes) * 100;
    const rawItemPct = (sz / totalBytes) * 100;
    const poolSavedPct = (poolSaved / totalBytes) * 100;
    const totalUsedAfterPct = (usedAfterPool / totalBytes) * 100;

    const retEl = document.getElementById("cret-" + c.id);
    const prvEl = document.getElementById("cprv-" + c.id);
    const othEl = document.getElementById("coth-" + c.id);
    const impEl = document.getElementById("cimp-" + c.id);
    const midEl = document.getElementById("cmid-" + c.id);
    const lblEl = document.getElementById("clbl-" + c.id);

    if (retEl) {
      retEl.style.width = retainedPct.toFixed(2) + "%";
    }
    if (prvEl) {
      prvEl.style.width = previewPct.toFixed(2) + "%";
      prvEl.style.minWidth = previewBytes > 0 ? "3px" : "0px";
    }
    if (othEl) othEl.style.width = otherSavedPct.toFixed(2) + "%";
    if (impEl) {
      impEl.style.width = thisItemPct.toFixed(2) + "%";
      impEl.style.minWidth = (isChecked && thisItemSaved > 0) ? "4px" : "0px";
    }
    if (midEl) {
      if (isChecked) {
        midEl.innerHTML = otherPoolSaved > 0
          ? `<i class="dot-item"></i><span style="color:var(--accent-clay)">当前项目 -${formatBytes(sz)} (${rawItemPct.toFixed(1)}%)</span><span style="color:var(--ink-muted);margin:0 3px">｜</span><i class="dot-pool"></i><span style="color:var(--tone-sage)">本池总清理 -${formatBytes(poolSaved)} (${poolSavedPct.toFixed(1)}%)</span>`
          : `<i class="dot-item"></i><span style="color:var(--accent-clay)">当前项目清理 -${formatBytes(sz)} (${rawItemPct.toFixed(1)}%)</span>`;
      } else {
        midEl.innerHTML = poolSaved > 0
          ? `<i class="dot-item-ghost"></i><span style="color:var(--ink-muted)">待选 -${formatBytes(sz)} (${rawItemPct.toFixed(1)}%)</span><span style="color:var(--ink-muted);margin:0 3px">｜</span><i class="dot-pool"></i><span style="color:var(--tone-sage)">本池总清理 -${formatBytes(poolSaved)} (${poolSavedPct.toFixed(1)}%)</span>`
          : `<i class="dot-item-ghost"></i><span style="color:var(--ink-muted)">待选 -${formatBytes(sz)} (${rawItemPct.toFixed(1)}%)</span>`;
      }
    }
    if (lblEl) {
      lblEl.innerHTML = `已用 <strong style="color:var(--ink-primary)">${formatBytes(usedAfterPool)}</strong> (${totalUsedAfterPct.toFixed(1)}%)`;
    }
  });
}

function updateVisualCharts(lastToggledItem = null) {
  const strip = document.getElementById("pool-selector-strip");
  if (strip && strip.children.length === 0 && (REPORT_DATA.storage_gauges || []).length > 0) {
    initGaugesDOM();
  }

  const savingsByGauge = {};
  let totalSavingsBytes = 0;

  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    const sz = Number(c.size_bytes) || 0;
    const gid = c.gauge_id || "";
    if (state.checked[c.id]) {
      totalSavingsBytes += sz;
      if (gid) savingsByGauge[gid] = (savingsByGauge[gid] || 0) + sz;
    }
  });

  const dockEl = document.getElementById("selected-gib");
  if (dockEl) dockEl.textContent = Math.max(0, totalSavingsBytes / (1024 ** 3)).toFixed(2) + " GiB";
  pulseElement(document.getElementById("sticky-summary"), "rebound", 220);

  // Active Pool Main Bar: Left = Total Capacity, Middle Progress Bar = [Used][Current Item][Other Cleaned][Free], Right = Used Capacity
  const gauges = REPORT_DATA.storage_gauges || [];
  const activePool = gauges.find(g => g.id === activePoolId) || gauges[0];

  // Determine focused item in each pool
  (REPORT_DATA.storage_gauges || []).forEach(g => {
    const total = Math.max(1, Number(g.total_bytes) || 1);
    const used = Number(g.used_bytes) || 0;
    const saved = Math.min(used, savingsByGauge[g.id] || 0);
    const newUsed = Math.max(0, used - saved);
    const newPct = (newUsed / total) * 100;
    const warnPct = Number(g.warn_used_pct || g.warn_pct) || 85;

    const poolChecked = (REPORT_DATA.candidates || []).filter(
      c => c.gauge_id === g.id && state.checked[c.id] && !state.whitelisted[c.id] && c.status !== "done" && Number(c.size_bytes) > 0
    );
    let poolFocus = null;
    if (window._focusedItemId) {
      poolFocus = poolChecked.find(c => c.id === window._focusedItemId) || null;
    }
    if (!poolFocus && poolChecked.length > 0) {
      poolFocus = poolChecked.slice().sort((a, b) => (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0))[0];
    }
    const fBytes = poolFocus ? Math.min(saved, Number(poolFocus.size_bytes) || 0) : 0;
    const oBytes = Math.max(0, saved - fBytes);
    const fPct = (fBytes / total) * 100;
    const oPct = (oBytes / total) * 100;

    updatePoolMiniBarState(g.id, newPct, warnPct);
    const focusMiniEl = document.getElementById("gfocus-" + g.id);
    if (focusMiniEl) {
      focusMiniEl.style.width = fPct.toFixed(2) + "%";
      focusMiniEl.style.minWidth = fBytes > 0 ? "2px" : "0px";
    }
    const reclaimEl = document.getElementById("greclaim-" + g.id);
    if (reclaimEl) {
      reclaimEl.style.width = oPct.toFixed(2) + "%";
      reclaimEl.style.minWidth = oBytes > 0 ? "2px" : "0px";
    }
    const pillEl = document.getElementById("grecpill-" + g.id);
    if (pillEl) {
      pillEl.textContent = saved > 0 ? `-${formatCompactBytes(saved)}` : "";
    }
    const usedEl = document.getElementById("gused-" + g.id);
    if (usedEl) {
      usedEl.textContent = formatCompactBytes(newUsed);
    }
  });

  if (activePool) {
    const total = Math.max(1, Number(activePool.total_bytes) || 1);
    const used = Number(activePool.used_bytes) || 0;
    const free = activePool.free_bytes != null ? Number(activePool.free_bytes) : Math.max(0, total - used);
    const saved = Math.min(used, savingsByGauge[activePool.id] || 0);
    const retained = Math.max(0, used - saved);

    const usedBeforePct = (used / total) * 100;
    const retainedPct = (retained / total) * 100;
    const reclaimPct = (saved / total) * 100;
    const freePct = (free / total) * 100;
    const freeAfterPct = ((free + saved) / total) * 100;

    const poolCheckedItems = (REPORT_DATA.candidates || []).filter(
      c => c.gauge_id === activePool.id && state.checked[c.id] && !state.whitelisted[c.id] && c.status !== "done" && Number(c.size_bytes) > 0
    );
    let focusItem = null;
    if (window._focusedItemId) {
      focusItem = poolCheckedItems.find(c => c.id === window._focusedItemId) || null;
    }
    if (!focusItem && lastToggledItem && lastToggledItem.gauge_id === activePool.id && state.checked[lastToggledItem.id]) {
      focusItem = lastToggledItem;
    }
    if (!focusItem && poolCheckedItems.length > 0) {
      focusItem = poolCheckedItems.slice().sort((a, b) => (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0))[0];
    }
    const focusBytes = focusItem ? Math.min(saved, Number(focusItem.size_bytes) || 0) : 0;
    const otherSavedBytes = Math.max(0, saved - focusBytes);
    const focusPct = (focusBytes / total) * 100;
    const restReclaimPct = Math.max(0, reclaimPct - focusPct);

    const retEl = document.getElementById("seg-retained");
    if (retEl) {
      retEl.style.width = Math.max(0, Math.min(100, retainedPct)).toFixed(2) + "%";
    }
    const itemSegEl = document.getElementById("seg-reclaim-item");
    if (itemSegEl) {
      itemSegEl.style.width = focusPct.toFixed(2) + "%";
      itemSegEl.style.minWidth = focusBytes > 0 ? "3px" : "0px";
    }
    const recEl = document.getElementById("seg-reclaim-mid");
    if (recEl) {
      const w = Math.max(0, Math.min(100, restReclaimPct));
      recEl.style.width = w.toFixed(2) + "%";
      recEl.style.padding = w > 12 ? "0 8px" : "0";
      recEl.style.minWidth = otherSavedBytes > 0 ? "2px" : "0px";
    }

    const cleanPoolName = stripLeadingEmoji(activePool.label || activePool.id);
    const titleEl = document.getElementById("anatomy-pool-title");
    if (titleEl) titleEl.textContent = cleanPoolName;
    const focusBadgeEl = document.getElementById("anatomy-focus-badge");
    if (focusBadgeEl) {
      if (focusItem && focusBytes > 0) {
        focusBadgeEl.style.display = "inline-block";
        focusBadgeEl.textContent = `当前项目 ${focusItem.id} -${formatBytes(focusBytes)} (${focusPct.toFixed(1)}%)`;
      } else {
        focusBadgeEl.style.display = "none";
      }
    }
    const mainTotalEl = document.getElementById("main-storage-total");
    if (mainTotalEl) mainTotalEl.textContent = `总容量 ${formatBytes(total)}`;
    const midBadgeEl = document.getElementById("anatomy-mid-badge");
    if (midBadgeEl) {
      midBadgeEl.textContent = `本池总清理 -${formatBytes(saved)} (${reclaimPct.toFixed(1)}%)`;
    }
    const subEl = document.getElementById("anatomy-pool-sub");
    if (subEl) {
      subEl.textContent =
        `清理前已用 ${formatBytes(used)} (${usedBeforePct.toFixed(1)}%) → 清理后已用 ${formatBytes(retained)} (${retainedPct.toFixed(1)}%)`;
    }
    const readoutEl = document.getElementById("anatomy-pool-readout");
    if (readoutEl) {
      readoutEl.innerHTML =
        `已用 <strong style="color:var(--ink-primary);font-size:13px">${formatBytes(retained)}</strong> (${retainedPct.toFixed(1)}%)`;
    }

    const isWideBar = window.innerWidth >= 760;
    if (retEl) retEl.textContent = (isWideBar && retainedPct > 22) ? `清理后已用 ${formatBytes(retained)} (${retainedPct.toFixed(1)}%)` : "";
    if (itemSegEl) {
      itemSegEl.textContent = (isWideBar && focusPct >= 14) ? `${focusItem ? focusItem.id : ""} -${formatCompactBytes(focusBytes)}` : ((isWideBar && focusPct >= 10.5) ? `-${formatCompactBytes(focusBytes)}` : "");
    }
    if (recEl) {
      if (isWideBar && restReclaimPct > 18) {
        recEl.textContent = focusBytes > 0
          ? `其他已选 -${formatBytes(otherSavedBytes)} (${restReclaimPct.toFixed(1)}%)`
          : `总清理 -${formatBytes(saved)} (${reclaimPct.toFixed(1)}%)`;
      } else if (isWideBar && restReclaimPct > 10) {
        recEl.textContent = focusBytes > 0 ? `其他 -${formatCompactBytes(otherSavedBytes)}` : `-${formatCompactBytes(saved)}`;
      } else {
        recEl.textContent = "";
      }
    }
    const freeEl = document.getElementById("seg-free");
    if (freeEl) freeEl.textContent = (isWideBar && freePct > 20) ? `原本空闲 ${formatBytes(free)} (${freePct.toFixed(1)}%)` : "";

    const legRetEl = document.getElementById("leg-retained");
    if (legRetEl) legRetEl.textContent = `${formatBytes(retained)} (${retainedPct.toFixed(1)}%)`;
    const focusLegEl = document.getElementById("leg-focus-item");
    if (focusLegEl) {
      focusLegEl.textContent = focusItem
        ? `${focusItem.id} -${formatBytes(focusBytes)} (${focusPct.toFixed(1)}%)`
        : "0 GiB (0%)";
    }
    const legRecEl = document.getElementById("leg-reclaim");
    if (legRecEl) legRecEl.textContent = `-${formatBytes(saved)} (${reclaimPct.toFixed(1)}%)`;
    const legFreeEl = document.getElementById("leg-free");
    if (legFreeEl) legFreeEl.textContent = `${formatBytes(free)} (${freePct.toFixed(1)}%)`;
    const legTotalFreeEl = document.getElementById("leg-total-free-after");
    if (legTotalFreeEl) {
      legTotalFreeEl.textContent = `清理后总可用 ${formatBytes(free + saved)} (${freeAfterPct.toFixed(1)}%)`;
    }

    renderPoolCategoryBreakdown(activePool, focusItem);
  }

  updateAllCardBars(savingsByGauge);

  // Update Spotlight Banner when user toggles any switch
  if (lastToggledItem) {
    const banner = document.getElementById("spotlight-banner");
    const textEl = document.getElementById("spotlight-text");
    const badgeEl = document.getElementById("spotlight-badge");
    const isNowChecked = !!state.checked[lastToggledItem.id];
    const sz = Number(lastToggledItem.size_bytes) || 0;
    const pool = (REPORT_DATA.storage_gauges || []).find(g => g.id === lastToggledItem.gauge_id);
    const poolSaved = pool ? (savingsByGauge[pool.id] || 0) : totalSavingsBytes;
    const poolUsedAfter = pool ? Math.max(0, (Number(pool.used_bytes) || 0) - poolSaved) : 0;

    if (sz > 0) {
      textEl.innerHTML = isNowChecked
        ? `已开启 <strong>${escapeHtml(lastToggledItem.id)} · ${escapeHtml(stripLeadingEmoji(lastToggledItem.title))}</strong>：当前项目清理 <strong style="color:var(--accent-clay)">-${formatBytes(sz)}</strong>，本池清理后已用降至 <strong>${formatBytes(poolUsedAfter)}</strong>`
        : `已关闭 <strong>${escapeHtml(lastToggledItem.id)} · ${escapeHtml(stripLeadingEmoji(lastToggledItem.title))}</strong>：移出清理 <strong style="color:var(--accent-clay)">${formatBytes(sz)}</strong>，本池清理后已用回升至 <strong>${formatBytes(poolUsedAfter)}</strong>`;
      badgeEl.textContent = isNowChecked ? `已清理 -${formatCompactBytes(sz)}` : `已恢复 +${formatCompactBytes(sz)}`;
    } else {
      textEl.innerHTML = `${isNowChecked ? "已开启" : "已关闭"}配置项 <strong>${escapeHtml(lastToggledItem.id)} · ${escapeHtml(stripLeadingEmoji(lastToggledItem.title))}</strong>`;
      badgeEl.textContent = isNowChecked ? "已纳入执行" : "已移除";
    }
    pulseElement(banner, "bump", 240);
  }
}

function renderPoolCategoryBreakdown(pool, focusItem = null) {
  const box = document.getElementById("category-breakdown-grid");
  if (!box) return;
  box.innerHTML = "";
  const poolItems = (REPORT_DATA.candidates || []).filter(c => c.gauge_id === pool.id && Number(c.size_bytes) > 0);
  const baseCategories = Array.isArray(pool.breakdown) ? pool.breakdown : null;
  const focusCatName = focusItem ? inferCategoryName(focusItem) : null;
  const focusItemBytes = focusItem ? (Number(focusItem.size_bytes) || 0) : 0;

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

  rows.forEach(r => {
    const catTotal = Math.max(1, r.total);
    const saved = Math.min(catTotal, r.saved);
    const remain = Math.max(0, catTotal - saved);
    const isFocusCat = focusCatName === r.name && focusItemBytes > 0 && saved > 0;
    const catFocusBytes = isFocusCat ? Math.min(saved, focusItemBytes) : 0;
    const catOtherSaved = Math.max(0, saved - catFocusBytes);

    // Exact 100% scale relative to this directory's pre-cleanup size
    const remainPct = (remain / catTotal) * 100;
    const focusPct = (catFocusBytes / catTotal) * 100;
    const otherSavedPct = (catOtherSaved / catTotal) * 100;
    const savedPct = (saved / catTotal) * 100;

    const div = document.createElement("div");
    div.className = "cat-bar-card tabular" + (isFocusCat ? " focused-cat" : "");
    div.innerHTML = `
      <div class="cat-bar-head">
        <span class="cat-bar-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
        <span class="cat-bar-clean-tag" style="color:${saved > 0 ? "var(--tone-sage)" : "var(--ink-muted)"}">${saved > 0 ? `清理 -${formatBytes(saved)} (${savedPct.toFixed(1)}%)` : "未选清理 (0%)"}</span>
      </div>
      <div class="cat-bar-row">
        <span class="cat-bar-total">原占 ${formatBytes(catTotal)}</span>
        <div class="cat-bar-track">
          <div class="cat-bar-remain" style="width:${remainPct.toFixed(1)}%"></div>
          <div class="cat-bar-focus" style="width:${focusPct.toFixed(1)}%" title="当前项目清理 ${formatBytes(catFocusBytes)}"></div>
          <div class="cat-bar-saved" style="width:${otherSavedPct.toFixed(1)}%" title="本目录清理 ${formatBytes(saved)}"></div>
        </div>
        <span class="cat-bar-val">已用 <strong style="color:var(--ink-primary)">${formatBytes(remain)}</strong> (${remainPct.toFixed(1)}%)</span>
      </div>
    `;
    box.appendChild(div);
  });
}

function highlightItem(itemId, active) {
  document.querySelectorAll(`[data-item-id="${itemId}"]`).forEach(el => {
    el.classList.toggle("highlighted", active);
  });
  const card = document.getElementById("card-" + itemId);
  if (card) card.classList.toggle("highlighted", active);
  if (active) {
    window._focusedItemId = itemId;
    updateVisualCharts();
  }
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
  if (!sec) return;
  if (!diff || ((!diff.gauge_deltas || !diff.gauge_deltas.length) && (!diff.top_growth_paths || !diff.top_growth_paths.length))) {
    sec.style.display = "none";
    return;
  }
  sec.style.display = "block";
  const dtEl = document.getElementById("diff-time");
  if (dtEl) {
    dtEl.textContent = `${diff.before_generated_at || "上次快照"} → ${diff.after_generated_at || "当前快照"}`;
  }

  const gBox = document.getElementById("diff-gauges");
  if (gBox) {
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
  }

  const pBox = document.getElementById("diff-growth");
  if (pBox) {
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
}

function inferActionType(c) {
  if (c.action_type) return c.action_type;
  const text = `${c.id || ""} ${c.title || ""} ${c.category || ""}`.toLowerCase();
  if (text.includes("更新") || text.includes("升级") || text.includes("outdated") || text.includes("upgrade") || text.includes("rustup")) {
    return "update";
  }
  if (text.includes("优化") || text.includes("压缩") || text.includes("pack") || text.includes("fstrim") || text.includes("规则") || text.includes("drift") || text.includes("修复") || text.includes("ci_failure")) {
    return "optimize";
  }
  if (text.includes("整理") || text.includes("归档") || text.includes("合入") || text.includes("推送") || text.includes("纳管") || text.includes("规范") || text.includes("软链") || text.includes("governance") || text.includes("unpushed") || text.includes("unlinked")) {
    return "organize";
  }
  return "cleanup";
}

function actionTypeMeta(c) {
  const t = inferActionType(c);
  if (t === "update") return { key: "update", label: "版本更新", cls: "action-update", rank: 1 };
  if (t === "optimize") return { key: "optimize", label: "性能优化", cls: "action-optimize", rank: 2 };
  if (t === "organize") return { key: "organize", label: "结构整理", cls: "action-organize", rank: 3 };
  return { key: "cleanup", label: "空间清理", cls: "action-cleanup", rank: 4 };
}

function isOptOrUpdate(c) {
  const t = inferActionType(c);
  return t === "update" || t === "optimize" || t === "organize";
}

function updateTabCounts() {
  const domainCounts = { all: 0, mac: 0, projects: 0, github: 0, nas: 0, whitelisted: 0 };
  const actionCounts = { all: 0, cleanup: 0, organize: 0, optimize: 0, update: 0 };

  (REPORT_DATA.candidates || []).forEach(c => {
    const isWl = !!state.whitelisted[c.id];
    const act = inferActionType(c);

    // 1. Domain counts (respecting currentAction filter for non-whitelisted tabs)
    if (isWl) {
      domainCounts.whitelisted++;
    } else if (currentAction === "all" || act === currentAction) {
      domainCounts.all++;
      if (domainCounts[c.domain] != null) domainCounts[c.domain]++;
    }

    // 2. Secondary action counts (respecting currentDomain filter)
    const matchesDomain = currentDomain === "whitelisted"
      ? isWl
      : (!isWl && (currentDomain === "all" || c.domain === currentDomain));
    if (matchesDomain) {
      actionCounts.all++;
      if (actionCounts[act] != null) actionCounts[act]++;
    }
  });

  Object.keys(domainCounts).forEach(k => {
    const el = document.getElementById("cnt-" + k);
    if (el) el.textContent = String(domainCounts[k]);
  });
  Object.keys(actionCounts).forEach(k => {
    const el = document.getElementById("acnt-" + k);
    if (el) el.textContent = String(actionCounts[k]);
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
  try { renderRingOverview(); } catch (e) {}
}

function setSortMode(mode, btnEl) {
  currentSort = mode;
  document.querySelectorAll(".sort-control button").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-sort") === mode);
  });
  renderCandidates();
}

function renderCandidates() {
  const list = document.getElementById("candidate-list");
  if (!list) return;
  list.innerHTML = "";
  let visibleIndex = 0;

  const items = (REPORT_DATA.candidates || []).slice();
  if (currentSort === "opt") {
    items.sort((a, b) => {
      const ra = actionTypeMeta(a).rank;
      const rb = actionTypeMeta(b).rank;
      if (ra !== rb) return ra - rb;
      return (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0);
    });
  } else if (currentSort === "size") {
    items.sort((a, b) => (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0));
  } else if (currentSort === "pool") {
    items.sort((a, b) => {
      const ga = String(a.gauge_id || "");
      const gb = String(b.gauge_id || "");
      if (ga !== gb) return ga.localeCompare(gb);
      return (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0);
    });
  }

  items.forEach(c => {
    const isWl = !!state.whitelisted[c.id];
    const isDone = c.status === "done";
    const isChecked = !isWl && !isDone && !!state.checked[c.id];

    if (currentDomain === "whitelisted") {
      if (!isWl) return;
    } else {
      if (isWl) return;
      if (currentDomain !== "all" && c.domain !== currentDomain) return;
    }

    const actMeta = actionTypeMeta(c);
    if (currentAction !== "all" && actMeta.key !== currentAction) return;

    const tier = c.tier || "B";
    const tierLabel = tier === "A" ? "A 级 · 零风险可重建" : (tier === "B" ? "B 级 · 推荐整理" : "C 级 · 需确认时机");
    const noteVal = state.notes[c.id] || c.whitelist_note || "";
    const sz = Number(c.size_bytes) || 0;
    const rightBadge = sz > 0 ? formatBytes(sz) : (c.metric_badge || formatBytes(0));
    const pShort = shortPoolName(c.gauge_id);

    // Left = Total Capacity, Middle Progress Bar = [Retained][Preview][This Item Cleaned][Other Checked][Free], Right = Used Capacity
    const { totalBytes, origUsedBytes } = getCardPoolMetrics(c);
    const curReclaimPct = isChecked ? (sz / totalBytes) * 100 : 0;
    const curPreviewPct = (!isChecked && !isWl && !isDone) ? (sz / totalBytes) * 100 : 0;
    const curRetainedPct = Math.max(0, ((origUsedBytes - sz) / totalBytes) * 100);

    const card = document.createElement("div");
    card.id = "card-" + c.id;
    card.className = "candidate-card" + (isChecked ? " checked" : "") + (isDone ? " done" : "") + (isWl ? " whitelisted" : "");
    card.style.animationDelay = `${Math.min(visibleIndex * 20, 160)}ms`;
    card.onmouseenter = () => highlightItem(c.id, true);
    card.onmouseleave = () => highlightItem(c.id, false);
    visibleIndex++;

    const impactBarHtml = sz > 0 ? `
      <div class="card-impact-meter tabular">
        <div class="card-impact-head">
          <span class="card-impact-total">总容量 ${formatBytes(totalBytes)}</span>
          <span class="card-impact-mid-tag" id="cmid-${escapeHtml(c.id)}">${isChecked ? `当前项目 -${formatBytes(sz)}` : `待选 -${formatBytes(sz)}`}</span>
          <span class="card-impact-label" id="clbl-${escapeHtml(c.id)}">
            已用 <strong style="color:var(--ink-primary)">${formatBytes(origUsedBytes)}</strong>
          </span>
        </div>
        <div class="card-impact-track">
          <div class="card-impact-retained" id="cret-${escapeHtml(c.id)}" style="width:${curRetainedPct.toFixed(2)}%"></div>
          <div class="card-impact-preview" id="cprv-${escapeHtml(c.id)}" style="width:${curPreviewPct.toFixed(2)}%"></div>
          <div class="card-impact-reclaim" id="cimp-${escapeHtml(c.id)}" style="width:${curReclaimPct.toFixed(2)}%"></div>
          <div class="card-impact-other" id="coth-${escapeHtml(c.id)}" style="width:0%"></div>
        </div>
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
          <span class="action-tag ${actMeta.cls}">${actMeta.label}</span>
          ${pShort ? `<span class="pool-badge">${escapeHtml(pShort)}</span>` : ""}
          <span class="tier-tag tier-${escapeHtml(tier)}">${tierLabel}</span>
          ${isDone ? '<span class="tier-tag tier-A">已执行</span>' : ""}
          ${isWl ? '<span class="tier-tag tier-C">已豁免</span>' : ""}
          <span class="candidate-size tabular">${escapeHtml(rightBadge)}</span>
        </div>
        <div class="candidate-title" onclick="clickCardTitle('${escapeHtml(c.id)}')">${escapeHtml(stripLeadingEmoji(c.title))}</div>
        ${impactBarHtml}
        <div class="candidate-meta-row">
          ${c.path ? `<div class="candidate-path">${escapeHtml(c.path)}</div>` : ""}
          <div class="candidate-evidence">${escapeHtml(c.evidence || "")}</div>
        </div>
        <div class="candidate-controls">
          <input type="text" class="note-input" placeholder="添加执行微调说明（如：保留最近 2 个版本 / 移至归档目录）"
            value="${escapeHtml(noteVal)}"
            oninput="updateNote('${escapeHtml(c.id)}', this.value)">
          <button class="btn-whitelist" onclick="toggleWhitelist('${escapeHtml(c.id)}')">
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
  if (c && c.gauge_id && c.gauge_id !== activePoolId) {
    activePoolId = c.gauge_id;
    document.querySelectorAll(".pool-tab-card").forEach(el => {
      el.classList.toggle("active", el.id === "pool-tab-" + activePoolId);
    });
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
}

function setDomain(domain, btnEl) {
  currentDomain = domain;
  document.querySelectorAll("#domain-tabs button").forEach(btn => {
    const isAct = btn.getAttribute("data-domain") === domain;
    btn.classList.toggle("active", isAct);
  });
  renderCandidates();
}

function setActionFilter(action, btnEl) {
  currentAction = action;
  document.querySelectorAll("#action-tabs button").forEach(btn => {
    const isAct = btn.getAttribute("data-action") === action;
    btn.classList.toggle("active", isAct);
  });
  renderCandidates();
}

function selectVisibleCategory() {
  (REPORT_DATA.candidates || []).forEach(c => {
    const isWl = !!state.whitelisted[c.id];
    if (c.status === "done" || isWl) return;
    if (currentDomain !== "all" && currentDomain !== "whitelisted" && c.domain !== currentDomain) return;
    const act = inferActionType(c);
    if (currentAction !== "all" && act !== currentAction) return;
    state.checked[c.id] = true;
  });
  saveState();
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

function selectOptimizations() {
  (REPORT_DATA.candidates || []).forEach(c => {
    if (c.status === "done" || state.whitelisted[c.id]) return;
    if (isOptOrUpdate(c)) state.checked[c.id] = true;
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
  safeStorageRemove(STORAGE_KEY);
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

let activeRingPoolId = ((REPORT_DATA.storage_gauges || [])[0] || {}).id || "_all";
let hoveredRingItemId = null;

// Interruptible Spring Animator State for Giant Donut Ring (r = 116, C = 728.8495)
const RING_RADIUS = 116;
const RING_CIRC = 2 * Math.PI * RING_RADIUS;
const ringAnim = {
  initialized: false,
  rafId: 0,
  lastTs: 0,
  cur: { retArc: 0, clnArc: 0, focArc: 0, bytes: 0 },
  vel: { retArc: 0, clnArc: 0, focArc: 0, bytes: 0 },
  tgt: { retArc: 0, clnArc: 0, focArc: 0, bytes: 0, isFocus: false }
};

function formatMiniBytes(bytes) {
  const b = Math.max(0, Math.abs(Number(bytes) || 0));
  if (b >= 1024 ** 4) return (b / (1024 ** 4)).toFixed(1) + " TiB";
  if (b >= 1024 ** 3) {
    const g = b / (1024 ** 3);
    return (g >= 10 ? g.toFixed(0) : g.toFixed(1)) + " GiB";
  }
  if (b >= 1024 ** 2) return (b / (1024 ** 2)).toFixed(0) + " MiB";
  return "0 GiB";
}

function formatNumberUnitPair(bytes, signed = true) {
  const b = Math.max(0, Math.abs(Number(bytes) || 0));
  const prefix = signed && b > 0 ? "-" : "";
  if (b >= 1024 ** 4) return { num: prefix + (b / (1024 ** 4)).toFixed(2), unit: "TiB" };
  if (b >= 1024 ** 3) return { num: prefix + (b / (1024 ** 3)).toFixed(2), unit: "GiB" };
  if (b >= 1024 ** 2) return { num: prefix + (b / (1024 ** 2)).toFixed(1), unit: "MiB" };
  if (b > 0) return { num: prefix + (b / 1024).toFixed(0), unit: "KiB" };
  return { num: "0.00", unit: "GiB" };
}

function applyRingFrame() {
  const C = RING_CIRC;
  const retArc = Math.max(0, Math.min(C, ringAnim.cur.retArc));
  const clnArc = Math.max(0, Math.min(C - retArc, ringAnim.cur.clnArc));
  const focArc = Math.max(0, Math.min(C - retArc - clnArc, ringAnim.cur.focArc));

  // Subtle 2px visual separation between retained used and cleaned highlight
  const hasCleanedOrFocus = (clnArc + focArc) > 1.5;
  const drawRetArc = (retArc > 4 && hasCleanedOrFocus) ? Math.max(0, retArc - 2) : retArc;

  const arcRetEl = document.getElementById("ring-arc-retained");
  const arcClnEl = document.getElementById("ring-arc-cleaned");
  const arcFocEl = document.getElementById("ring-arc-focus");

  if (arcRetEl) {
    arcRetEl.setAttribute("stroke-dasharray", `${drawRetArc.toFixed(2)} ${C.toFixed(2)}`);
    arcRetEl.setAttribute("stroke-dashoffset", "0");
  }
  if (arcClnEl) {
    arcClnEl.setAttribute("stroke-dasharray", `${clnArc.toFixed(2)} ${C.toFixed(2)}`);
    arcClnEl.setAttribute("stroke-dashoffset", `-${retArc.toFixed(2)}`);
  }
  if (arcFocEl) {
    arcFocEl.setAttribute("stroke-dasharray", `${focArc.toFixed(2)} ${C.toFixed(2)}`);
    arcFocEl.setAttribute("stroke-dashoffset", `-${(retArc + clnArc).toFixed(2)}`);
  }

  const numEl = document.getElementById("ring-center-num");
  const unitEl = document.getElementById("ring-center-unit");
  if (numEl && unitEl) {
    const pair = formatNumberUnitPair(ringAnim.cur.bytes, true);
    numEl.textContent = pair.num;
    unitEl.textContent = pair.unit;
    const toneColor = ringAnim.tgt.isFocus ? "var(--accent-clay)" : "var(--tone-sage)";
    numEl.style.color = toneColor;
    unitEl.style.color = toneColor;
  }
}

function stepRingSpring(ts) {
  if (!ringAnim.lastTs) ringAnim.lastTs = ts;
  const dt = Math.min(0.032, Math.max(0.001, (ts - ringAnim.lastTs) / 1000));
  ringAnim.lastTs = ts;

  // Critically damped spring (omega = 20, zeta = 0.92) for responsive interruptible motion
  const omega = 20;
  const zeta = 0.92;
  const keys = ["retArc", "clnArc", "focArc", "bytes"];
  let stillMoving = false;

  keys.forEach(k => {
    const x = ringAnim.cur[k] - ringAnim.tgt[k];
    const v = ringAnim.vel[k];
    const accel = -(omega * omega) * x - (2 * zeta * omega) * v;
    const nextV = v + accel * dt;
    const nextX = ringAnim.cur[k] + nextV * dt;
    ringAnim.vel[k] = nextV;
    ringAnim.cur[k] = nextX;

    const tol = k === "bytes" ? Math.max(1024 * 1024, Math.abs(ringAnim.tgt.bytes) * 0.0015) : 0.18;
    if (Math.abs(nextX - ringAnim.tgt[k]) > tol || Math.abs(nextV) > tol * 3) {
      stillMoving = true;
    } else {
      ringAnim.cur[k] = ringAnim.tgt[k];
      ringAnim.vel[k] = 0;
    }
  });

  applyRingFrame();
  if (stillMoving) {
    ringAnim.rafId = window.requestAnimationFrame(stepRingSpring);
  } else {
    ringAnim.rafId = 0;
    ringAnim.lastTs = 0;
  }
}

function animateRingTo(retArc, clnArc, focArc, displayBytes, isFocus) {
  ringAnim.tgt = { retArc, clnArc, focArc, bytes: displayBytes, isFocus };
  if (!ringAnim.initialized || REDUCED_MOTION || !window.requestAnimationFrame) {
    ringAnim.initialized = true;
    ringAnim.cur = { retArc, clnArc, focArc, bytes: displayBytes };
    ringAnim.vel = { retArc: 0, clnArc: 0, focArc: 0, bytes: 0 };
    applyRingFrame();
    return;
  }
  if (!ringAnim.rafId) {
    ringAnim.lastTs = 0;
    ringAnim.rafId = window.requestAnimationFrame(stepRingSpring);
  }
}

function switchMainView(mode) {
  const ringPage = document.getElementById("ring-overview-page");
  const wbPage = document.getElementById("workbench-page");
  const btnRing = document.getElementById("view-btn-ring");
  const btnWb = document.getElementById("view-btn-workbench");
  const wbCluster = document.getElementById("workbench-action-cluster");
  const topRingSum = document.getElementById("top-ring-summary");
  const isRing = mode !== "workbench";
  if (ringPage) ringPage.style.display = isRing ? "block" : "none";
  if (wbPage) wbPage.style.display = isRing ? "none" : "block";
  if (btnRing) btnRing.classList.toggle("active", isRing);
  if (btnWb) btnWb.classList.toggle("active", !isRing);
  if (wbCluster) wbCluster.style.display = isRing ? "none" : "inline-flex";
  if (topRingSum) topRingSum.style.display = isRing ? "inline-flex" : "none";
}

function concisePoolTitle(g) {
  const id = String(g.id || "");
  if (id === "_all") return "全盘存储总览";
  if (id === "mac-data") return "Mac 本机磁盘";
  if (id === "nas-data") return "NAS 主存储池";
  if (id === "sora2t") return "Sora2T 固态盘";
  if (id === "sora4t") return "Sora4T 机械盘";
  if (id === "nas-pve-backups") return "NAS NVMe 池";
  if (id === "pve-lvm") return "PVE Thin 池";
  if (id === "pve-root") return "PVE 宿主机";
  if (id === "pt-docker-root") return "PT-Docker";
  const raw = stripLeadingEmoji(g.label || id);
  return raw.replace(/\\s*\\(.*?\\)\\s*/g, "").trim();
}

function computePoolRingMetrics(g) {
  const total = Math.max(1, Number(g.total_bytes) || 1);
  const currentUsed = Number(g.used_bytes) || 0;
  const currentFree = g.free_bytes != null ? Number(g.free_bytes) : Math.max(0, total - currentUsed);

  const poolCands = (REPORT_DATA.candidates || []).filter(c => c.gauge_id === g.id && !state.whitelisted[c.id] && !c.whitelisted);
  let doneBytes = 0;
  let checkedBytes = 0;
  poolCands.forEach(c => {
    const sz = Number(c.size_bytes) || 0;
    if (sz <= 0) return;
    if (c.status === "done") doneBytes += sz;
    else if (state.checked[c.id]) checkedBytes += sz;
  });

  const explicitCleaned = Number(g.cleaned_bytes) || 0;
  const hasDone = doneBytes > 0 || explicitCleaned > 0 || g.before_used_bytes != null;

  let retainedBytes = currentUsed;
  let cleanedBytes = 0;
  let beforeUsedBytes = currentUsed;
  let freeAfterBytes = currentFree;
  let freeBeforeBytes = currentFree;

  if (hasDone) {
    const baseCleaned = doneBytes > 0 ? doneBytes : explicitCleaned;
    cleanedBytes = Math.min(total, baseCleaned + checkedBytes);
    retainedBytes = Math.max(0, Math.min(total - cleanedBytes, currentUsed - checkedBytes));
    beforeUsedBytes = Math.min(total, retainedBytes + cleanedBytes);
    freeAfterBytes = Math.max(0, total - retainedBytes);
    freeBeforeBytes = Math.max(0, total - beforeUsedBytes);
  } else {
    cleanedBytes = Math.min(currentUsed, checkedBytes);
    retainedBytes = Math.max(0, currentUsed - cleanedBytes);
    beforeUsedBytes = currentUsed;
    freeBeforeBytes = currentFree;
    freeAfterBytes = currentFree + cleanedBytes;
  }

  const beforePct = g.before_used_pct != null && checkedBytes === 0
    ? Number(g.before_used_pct)
    : Math.min(100, (beforeUsedBytes / total) * 100);
  const afterPct = Math.min(100, (retainedBytes / total) * 100);

  return {
    id: g.id,
    label: stripLeadingEmoji(g.label || g.id),
    shortTitle: concisePoolTitle(g),
    mountPath: g.mount_path || "",
    total,
    retainedBytes,
    cleanedBytes,
    beforeUsedBytes,
    freeBeforeBytes,
    freeAfterBytes,
    beforePct,
    afterPct,
    hasDone
  };
}

function selectRingPool(poolId) {
  activeRingPoolId = poolId;
  hoveredRingItemId = null;
  renderRingOverview(false);
}

function highlightRingItem(itemId, active) {
  hoveredRingItemId = active ? itemId : null;
  renderRingOverview(true);
}

let _miniRingsRenderedCount = 0;

function renderRingOverview(onlyUpdateArcs = false) {
  const gauges = REPORT_DATA.storage_gauges || [];
  if (!gauges.length) return;

  const metricsList = gauges.map(computePoolRingMetrics);
  const allAgg = metricsList.reduce((acc, m) => {
    acc.total += m.total;
    acc.retainedBytes += m.retainedBytes;
    acc.cleanedBytes += m.cleanedBytes;
    acc.beforeUsedBytes += m.beforeUsedBytes;
    acc.freeBeforeBytes += m.freeBeforeBytes;
    acc.freeAfterBytes += m.freeAfterBytes;
    acc.hasDone = acc.hasDone || m.hasDone;
    return acc;
  }, {
    id: "_all",
    label: "全盘存储总览 (Mac + NAS + Sora2T + Sora4T + PVE)",
    shortTitle: "全盘存储总览",
    mountPath: "All Mounted Volumes",
    total: 0,
    retainedBytes: 0,
    cleanedBytes: 0,
    beforeUsedBytes: 0,
    freeBeforeBytes: 0,
    freeAfterBytes: 0,
    beforePct: 0,
    afterPct: 0,
    hasDone: false
  });
  if (allAgg.total > 0) {
    allAgg.beforePct = (allAgg.beforeUsedBytes / allAgg.total) * 100;
    allAgg.afterPct = (allAgg.retainedBytes / allAgg.total) * 100;
  }

  // Update Top Command Bar & Header global summary pills
  const allCands = (REPORT_DATA.candidates || []).slice();
  const totalSpaceItems = allCands.filter(c => !state.whitelisted[c.id] && !c.whitelisted && (c.status === "done" || state.checked[c.id]) && (Number(c.size_bytes) || 0) > 0).length;
  const totalOptItems = allCands.filter(c => !state.whitelisted[c.id] && !c.whitelisted && (c.status === "done" || state.checked[c.id]) && (Number(c.size_bytes) || 0) === 0).length;
  const totalProtItems = allCands.filter(c => state.whitelisted[c.id] || c.whitelisted).length;

  const topSumEl = document.getElementById("top-ring-summary");
  if (topSumEl) {
    topSumEl.textContent = `✨ 全栈累计释放 -${formatCompactBytes(allAgg.cleanedBytes)} · ${totalSpaceItems + totalOptItems} 项优化已生效`;
  }
  const headerPillsEl = document.getElementById("header-kpi-pills");
  if (headerPillsEl && !onlyUpdateArcs) {
    headerPillsEl.innerHTML = `
      <span class="header-kpi-pill"><i class="legend-dot" style="background:var(--tone-sage)"></i>空间清理 <strong>${totalSpaceItems} 项 (-${formatCompactBytes(allAgg.cleanedBytes)})</strong></span>
      <span class="header-kpi-pill"><i class="legend-dot" style="background:var(--accent-clay)"></i>系统与管线优化 <strong>${totalOptItems} 项</strong></span>
      <span class="header-kpi-pill"><i class="legend-dot" style="background:var(--tone-ochre)"></i>白名单与整树保护 <strong>${totalProtItems} 项</strong></span>
    `;
  }

  const matrixOrder = [...metricsList, allAgg];
  const activeMetrics = activeRingPoolId === "_all"
    ? allAgg
    : (metricsList.find(m => m.id === activeRingPoolId) || metricsList[0]);

  // 1. Render or update 3x3 Storage Pool Matrix without destroying DOM on pool switch
  const miniStrip = document.getElementById("mini-rings-strip");
  if (miniStrip && !onlyUpdateArcs) {
    const hintEl = document.getElementById("mini-matrix-hint");
    if (hintEl) hintEl.textContent = `共 ${matrixOrder.length} 个存储视图 · 当前选中：${activeMetrics.shortTitle}`;

    if (_miniRingsRenderedCount !== matrixOrder.length || miniStrip.children.length !== matrixOrder.length) {
      miniStrip.innerHTML = "";
      matrixOrder.forEach(m => {
        const card = document.createElement("div");
        card.id = "mring-card-" + m.id;
        card.className = "mini-ring-card tabular" + (m.id === activeMetrics.id ? " active" : "");
        card.onclick = () => selectRingPool(m.id);
        card.innerHTML = `
          <div class="mini-ring-wrap">
            <svg class="mini-ring-svg" viewBox="0 0 40 40">
              <circle cx="20" cy="20" r="15.5" fill="none" stroke="var(--bar-free)" stroke-width="4.5"></circle>
              <circle id="mring-ret-${escapeHtml(m.id)}" cx="20" cy="20" r="15.5" fill="none" stroke="var(--bar-retained)" stroke-width="4.5"
                stroke-dasharray="0 97.39" stroke-dashoffset="0"></circle>
              <circle id="mring-cln-${escapeHtml(m.id)}" cx="20" cy="20" r="15.5" fill="none" stroke="var(--tone-sage)" stroke-width="4.5"
                stroke-dasharray="0 97.39" stroke-dashoffset="0"></circle>
            </svg>
            <span class="mini-ring-pct" id="mring-pct-${escapeHtml(m.id)}">0%</span>
          </div>
          <div class="mini-ring-info">
            <div class="mini-ring-row1">
              <span class="mini-ring-name" title="${escapeHtml(m.label)}">${escapeHtml(m.shortTitle)}</span>
              <span class="mini-ring-saved" id="mring-saved-${escapeHtml(m.id)}"></span>
            </div>
            <div class="mini-ring-meta" id="mring-meta-${escapeHtml(m.id)}"></div>
          </div>
        `;
        miniStrip.appendChild(card);
      });
      _miniRingsRenderedCount = matrixOrder.length;
    }

    const cMini = 2 * Math.PI * 15.5;
    matrixOrder.forEach(m => {
      const card = document.getElementById("mring-card-" + m.id);
      if (card) card.classList.toggle("active", m.id === activeMetrics.id);
      const retLen = Math.max(0, Math.min(cMini, (m.retainedBytes / m.total) * cMini));
      const clnLen = Math.max(0, Math.min(cMini - retLen, (m.cleanedBytes / m.total) * cMini));
      const retCircle = document.getElementById("mring-ret-" + m.id);
      const clnCircle = document.getElementById("mring-cln-" + m.id);
      const pctSpan = document.getElementById("mring-pct-" + m.id);
      const savedSpan = document.getElementById("mring-saved-" + m.id);
      const metaSpan = document.getElementById("mring-meta-" + m.id);

      if (retCircle) {
        retCircle.setAttribute("stroke-dasharray", `${retLen.toFixed(2)} ${cMini.toFixed(2)}`);
        retCircle.setAttribute("stroke-dashoffset", "0");
      }
      if (clnCircle) {
        clnCircle.setAttribute("stroke-dasharray", `${clnLen.toFixed(2)} ${cMini.toFixed(2)}`);
        clnCircle.setAttribute("stroke-dashoffset", `-${retLen.toFixed(2)}`);
      }
      if (pctSpan) pctSpan.textContent = `${m.afterPct.toFixed(0)}%`;
      if (savedSpan) {
        if (m.cleanedBytes > 0) {
          savedSpan.className = "mini-ring-saved";
          savedSpan.textContent = `-${formatMiniBytes(m.cleanedBytes)}`;
        } else {
          savedSpan.className = "mini-ring-saved neutral";
          savedSpan.textContent = `正常`;
        }
      }
      if (metaSpan) {
        metaSpan.textContent = m.cleanedBytes > 0
          ? `水位 ${m.beforePct.toFixed(0)}%→${m.afterPct.toFixed(0)}% · 总 ${formatMiniBytes(m.total)}`
          : `可用 ${formatMiniBytes(m.freeAfterBytes)} / 总 ${formatMiniBytes(m.total)}`;
      }
    });
  }

  // 2. Compute & Animate Giant Donut Ring arcs (r = 116, C = 728.8495)
  const C = RING_CIRC;
  const total = Math.max(1, activeMetrics.total);
  const retained = Math.min(total, activeMetrics.retainedBytes);
  const cleaned = Math.min(total - retained, activeMetrics.cleanedBytes);

  let focusBytes = 0;
  let hoveredItemObj = null;
  if (hoveredRingItemId) {
    const hovered = allCands.find(c => c.id === hoveredRingItemId);
    if (hovered && (activeMetrics.id === "_all" || hovered.gauge_id === activeMetrics.id)) {
      focusBytes = Math.min(cleaned, Number(hovered.size_bytes) || 0);
      hoveredItemObj = hovered;
    }
  }
  const otherCleaned = Math.max(0, cleaned - focusBytes);

  const retArc = (retained / total) * C;
  const clnArc = (otherCleaned / total) * C;
  const focArc = (focusBytes / total) * C;
  const targetCenterBytes = focusBytes > 0 ? focusBytes : cleaned;

  animateRingTo(retArc, clnArc, focArc, targetCenterBytes, focusBytes > 0);

  const kickerEl = document.getElementById("ring-center-kicker");
  if (kickerEl) {
    if (focusBytes > 0 && hoveredItemObj) {
      kickerEl.textContent = `当前悬停项 (${ ((focusBytes / total) * 100).toFixed(1) }%)`;
    } else {
      kickerEl.textContent = activeMetrics.hasDone ? "本次已清掉空间" : "预计可清掉空间";
    }
  }
  const waterEl = document.getElementById("ring-center-water");
  if (waterEl) {
    waterEl.textContent = activeMetrics.cleanedBytes > 0
      ? `水位 ${activeMetrics.beforePct.toFixed(1)}% → ${activeMetrics.afterPct.toFixed(1)}%`
      : `当前水位 ${activeMetrics.afterPct.toFixed(1)}%`;
  }
  const subEl = document.getElementById("ring-center-sub");
  if (subEl) {
    subEl.textContent = `可用 ${formatCompactBytes(activeMetrics.freeAfterBytes)} / 总 ${formatCompactBytes(total)}`;
  }

  // Left-column 3-segment legend values
  const rlegRet = document.getElementById("rleg-retained");
  if (rlegRet) rlegRet.textContent = formatCompactBytes(retained);
  const rlegCln = document.getElementById("rleg-cleaned");
  if (rlegCln) rlegCln.textContent = cleaned > 0 ? `-${formatCompactBytes(cleaned)}` : "0 GiB";
  const rlegFree = document.getElementById("rleg-free");
  if (rlegFree) rlegFree.textContent = formatCompactBytes(activeMetrics.freeAfterBytes);

  // Right-column header & 4 KPI cards
  const titleEl = document.getElementById("ring-active-title");
  if (titleEl) titleEl.textContent = activeMetrics.shortTitle;
  const mountEl = document.getElementById("ring-active-mount");
  if (mountEl) mountEl.textContent = activeMetrics.mountPath || activeMetrics.id;
  const badgeEl = document.getElementById("ring-active-badge");
  if (badgeEl) {
    const savedPct = ((cleaned / total) * 100).toFixed(1);
    badgeEl.textContent = cleaned > 0
      ? `本次净释放 -${formatCompactBytes(cleaned)} (${savedPct}% 容量)`
      : `运行正常 · 无需清理`;
  }

  const rkpiTotal = document.getElementById("rkpi-total");
  if (rkpiTotal) rkpiTotal.textContent = formatCompactBytes(total);
  const rkpiBeforeUsed = document.getElementById("rkpi-before-used");
  if (rkpiBeforeUsed) rkpiBeforeUsed.textContent = `清理前已用 ${formatCompactBytes(activeMetrics.beforeUsedBytes)}`;

  const rkpiCleaned = document.getElementById("rkpi-cleaned");
  if (rkpiCleaned) rkpiCleaned.textContent = cleaned > 0 ? `-${formatCompactBytes(cleaned)}` : "0 GiB";
  const rkpiCleanedPct = document.getElementById("rkpi-cleaned-pct");
  if (rkpiCleanedPct) rkpiCleanedPct.textContent = `占全盘总容量 ${((cleaned / total) * 100).toFixed(1)}%`;

  const rkpiRetained = document.getElementById("rkpi-retained");
  if (rkpiRetained) rkpiRetained.textContent = formatCompactBytes(retained);
  const rkpiRetainedPct = document.getElementById("rkpi-retained-pct");
  if (rkpiRetainedPct) rkpiRetainedPct.textContent = `水位 ${activeMetrics.beforePct.toFixed(1)}% → ${activeMetrics.afterPct.toFixed(1)}%`;

  const rkpiFreeAfter = document.getElementById("rkpi-free-after");
  if (rkpiFreeAfter) rkpiFreeAfter.textContent = formatCompactBytes(activeMetrics.freeAfterBytes);
  const rkpiFreeBefore = document.getElementById("rkpi-free-before");
  if (rkpiFreeBefore) rkpiFreeBefore.textContent = `清理前可用 ${formatCompactBytes(activeMetrics.freeBeforeBytes)}`;

  if (onlyUpdateArcs) return;

  // 3. Render Detailed Breakdown Lists directly below the Giant Ring (Strict 3-Column Alignment)
  const detailBox = document.getElementById("ring-detailed-lists");
  if (!detailBox) return;
  detailBox.innerHTML = "";

  const filteredCands = activeMetrics.id === "_all"
    ? allCands
    : allCands.filter(c => !c.gauge_id || c.gauge_id === activeMetrics.id);

  const spaceCleanedItems = filteredCands
    .filter(c => !state.whitelisted[c.id] && !c.whitelisted && (c.status === "done" || state.checked[c.id]) && (Number(c.size_bytes) || 0) > 0)
    .sort((a, b) => (Number(b.size_bytes) || 0) - (Number(a.size_bytes) || 0));

  const sysOptItems = filteredCands
    .filter(c => !state.whitelisted[c.id] && !c.whitelisted && (c.status === "done" || state.checked[c.id]) && (Number(c.size_bytes) || 0) === 0);

  const protectedItems = (activeMetrics.id === "_all"
    ? allCands
    : allCands.filter(c => !c.gauge_id || c.gauge_id === activeMetrics.id)
  ).filter(c => state.whitelisted[c.id] || c.whitelisted);

  const maxCleanedItemBytes = Math.max(1, ...spaceCleanedItems.map(c => Number(c.size_bytes) || 0));
  const spaceSum = spaceCleanedItems.reduce((s, c) => s + (Number(c.size_bytes) || 0), 0);

  // Section A: Space Cleanup Detailed Rows
  const secSpace = document.createElement("section");
  secSpace.className = "detail-group-block";
  secSpace.innerHTML = `
    <div class="detail-group-head">
      <h3 class="detail-group-title">
        <span style="color:var(--tone-sage)">●</span>
        本次空间清理明细（${escapeHtml(activeMetrics.shortTitle)} · 共 ${spaceCleanedItems.length} 项 · 累计释放 <span class="mono" style="color:var(--tone-sage)">-${formatCompactBytes(spaceSum)}</span>）
      </h3>
      <span class="section-caption">按清理释放容量从大到小排列 · 悬停任意行可在上方圆环中以陶土橙高亮该项占比</span>
    </div>
    <div class="detail-rows" id="detail-space-rows"></div>
  `;
  detailBox.appendChild(secSpace);
  const spaceRowsEl = secSpace.querySelector("#detail-space-rows");
  if (spaceCleanedItems.length === 0) {
    spaceRowsEl.innerHTML = `
      <div class="detail-row">
        <div class="drow-idx">—</div>
        <div class="drow-main">
          <div class="drow-title-line"><span class="drow-title-text">当前存储卷无需清理（空间水位健康）</span></div>
          <div class="drow-sub-line">可点击右上方存储卷矩阵切换查看「全盘存储总览」「Mac 本机磁盘」「Sora4T 机械盘」「Sora2T 固态盘」或「NAS 主存储池」的清理明细。</div>
        </div>
        <div class="drow-right"><span class="drow-share">0 项待清理</span></div>
      </div>
    `;
  } else {
    spaceCleanedItems.forEach((c, idx) => {
      const sz = Number(c.size_bytes) || 0;
      const barPct = Math.max(4, (sz / maxCleanedItemBytes) * 100);
      const poolPct = ((sz / total) * 100).toFixed(1);
      const cleanSharePct = ((sz / Math.max(1, spaceSum)) * 100).toFixed(1);
      const pName = shortPoolName(c.gauge_id) || c.domain || "系统";
      const statusBadge = c.status === "done" ? "已清理释放" : "已勾选待清理";
      const row = document.createElement("div");
      row.className = "detail-row tabular";
      row.onmouseenter = () => highlightRingItem(c.id, true);
      row.onmouseleave = () => highlightRingItem(c.id, false);
      row.innerHTML = `
        <div class="drow-idx">${String(idx + 1).padStart(2, "0")}</div>
        <div class="drow-main">
          <div class="drow-title-line">
            <span class="drow-title-text">${escapeHtml(stripLeadingEmoji(c.title))}</span>
          </div>
          <div class="drow-meta-strip">
            <span class="pool-badge">${escapeHtml(pName)}</span>
            <span class="tier-tag tier-A">${statusBadge}</span>
            ${c.path ? `<code class="drow-path" title="${escapeHtml(c.path)}">${escapeHtml(c.path)}</code>` : ""}
          </div>
          <div class="drow-sub-line">${escapeHtml(c.evidence || "")}</div>
        </div>
        <div class="drow-right">
          <div class="drow-amount is-space">-${formatBytes(sz)}</div>
          <div class="drow-share">占盘 ${poolPct}% · 占清理 ${cleanSharePct}%</div>
          <div class="drow-bar-track">
            <div class="drow-bar-fill" style="width:${barPct.toFixed(1)}%"></div>
          </div>
        </div>
      `;
      spaceRowsEl.appendChild(row);
    });
  }

  // Section B: System & Backup Optimization Rows
  if (sysOptItems.length > 0) {
    const secOpt = document.createElement("section");
    secOpt.className = "detail-group-block";
    secOpt.innerHTML = `
      <div class="detail-group-head">
        <h3 class="detail-group-title">
          <span style="color:var(--accent-clay)">●</span>
          系统、备份管线与远端治理优化（共 ${sysOptItems.length} 项）
        </h3>
        <span class="section-caption">修复底层重传病灶、活跃工作树隔离保护与远端分支整理</span>
      </div>
      <div class="detail-rows" id="detail-opt-rows"></div>
    `;
    detailBox.appendChild(secOpt);
    const optRowsEl = secOpt.querySelector("#detail-opt-rows");
    sysOptItems.forEach((c, idx) => {
      const actMeta = actionTypeMeta(c);
      const row = document.createElement("div");
      row.className = "detail-row tabular";
      row.innerHTML = `
        <div class="drow-idx">${String(idx + 1).padStart(2, "0")}</div>
        <div class="drow-main">
          <div class="drow-title-line">
            <span class="drow-title-text">${escapeHtml(stripLeadingEmoji(c.title))}</span>
          </div>
          <div class="drow-meta-strip">
            <span class="action-tag ${actMeta.cls}">${actMeta.label}</span>
            <span class="tier-tag tier-A">${c.status === "done" ? "已完成优化" : "待执行"}</span>
            ${c.path ? `<code class="drow-path" title="${escapeHtml(c.path)}">${escapeHtml(c.path)}</code>` : ""}
          </div>
          <div class="drow-sub-line">${escapeHtml(c.evidence || "")}</div>
        </div>
        <div class="drow-right">
          <div class="drow-amount" style="font-size:13px">${escapeHtml(c.metric_badge || "架构与配置修复")}</div>
          <div class="drow-share">零空间风险 · 已生效</div>
        </div>
      `;
      optRowsEl.appendChild(row);
    });
  }

  // Section C: Protected & Whitelisted Items
  if (protectedItems.length > 0) {
    const secProt = document.createElement("section");
    secProt.className = "detail-group-block";
    secProt.innerHTML = `
      <div class="detail-group-head">
        <h3 class="detail-group-title">
          <span style="color:var(--tone-ochre)">🛡️</span>
          自动整树豁免与长期白名单保护（共 ${protectedItems.length} 项）
        </h3>
        <span class="section-caption">活跃 AI 工作树整树连带保护 + 用户指定跨盘双份备份目录永久豁免</span>
      </div>
      <div class="detail-rows" id="detail-prot-rows"></div>
    `;
    detailBox.appendChild(secProt);
    const protRowsEl = secProt.querySelector("#detail-prot-rows");
    protectedItems.forEach((c, idx) => {
      const sz = Number(c.size_bytes) || 0;
      const pName = shortPoolName(c.gauge_id) || c.domain || "系统";
      const note = state.notes[c.id] || c.whitelist_note || "受白名单或活跃工作树整树保护";
      const row = document.createElement("div");
      row.className = "detail-row tabular";
      row.innerHTML = `
        <div class="drow-idx">🛡️</div>
        <div class="drow-main">
          <div class="drow-title-line">
            <span class="drow-title-text">${escapeHtml(stripLeadingEmoji(c.title))}</span>
          </div>
          <div class="drow-meta-strip">
            <span class="pool-badge">${escapeHtml(pName)}</span>
            <span class="tier-tag tier-C">已豁免保护</span>
            ${c.path ? `<code class="drow-path" title="${escapeHtml(c.path)}">${escapeHtml(c.path)}</code>` : ""}
          </div>
          <div class="drow-sub-line"><strong>保护原因：</strong>${escapeHtml(note)} · ${escapeHtml(c.evidence || "")}</div>
        </div>
        <div class="drow-right">
          <div class="drow-amount" style="color:var(--ink-primary);font-size:13.5px">${sz > 0 ? `保留 ${formatBytes(sz)}` : "已锁定保护"}</div>
          <div class="drow-share">100% 完好跳过</div>
        </div>
      `;
      protRowsEl.appendChild(row);
    });
  }
}

function bootstrapDashboard() {
  try { initTheme(); } catch (e) {}
  try { initGaugesDOM(); } catch (e) {}
  try { renderDiff(); } catch (e) {}
  try { renderCandidates(); } catch (e) {}
  try { renderRingOverview(); } catch (e) {}
}

bootstrapDashboard();
window.addEventListener("DOMContentLoaded", () => {
  const list = document.getElementById("candidate-list");
  if (list && list.children.length === 0 && (REPORT_DATA.candidates || []).length > 0) {
    bootstrapDashboard();
  }
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
