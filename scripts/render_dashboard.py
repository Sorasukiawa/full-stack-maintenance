#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Design system:
- macOS-style app window: translucent vibrancy sidebar with System Settings style icon tiles,
  large titles, inset grouped lists, and a floating glass action dock.
- Overview: one thick storage ring drawn like an Apple Health activity ring (round caps,
  gradient along the arc) with retained used / selected to free / hovered item segments,
  per-volume mini rings, and a segmented "space composition" bar.
- Motion: every interactive change runs on real spring physics described with Apple's two
  designer parameters (damping ratio + response). Springs retarget from the current value and
  keep their velocity, so all motion is interruptible. CSS transitions use spring curves that
  are pre-computed here and emitted as ``linear()`` easing functions.
- Follows system light/dark appearance by default, honours reduced motion, reduced
  transparency and increased contrast preferences.

The script is read-only: it renders HTML from snapshot JSON and never touches the files
described in the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from pathlib import Path
from typing import Any


# Apple-style spring presets (damping ratio, response in seconds).
# damping < 1 gives the light rebound; response is how quickly the value arrives.
SPRING_PRESETS: dict[str, dict[str, float]] = {
    "bounce": {"damping": 0.72, "response": 0.42},  # 勾选、按钮回弹、Dock 数字
    "glide": {"damping": 0.80, "response": 0.34},   # 分段控件滑块、侧栏选中块
    "ring": {"damping": 0.78, "response": 0.62},    # 圆环与水位条
    "sheet": {"damping": 0.84, "response": 0.44},   # 弹出面板、菜单
    "soft": {"damping": 0.92, "response": 0.36},    # 页面切换、展开收起（几乎无回弹）
}


def spring_easing(damping: float, response: float, samples: int = 40) -> tuple[str, int]:
    """Sample an under-damped spring into a CSS ``linear()`` easing and its settle duration (ms)."""
    zeta = min(0.999, max(0.05, damping))
    omega = 2 * math.pi / response
    decay = zeta * omega
    duration = -math.log(0.001) / decay
    damped = omega * math.sqrt(1 - zeta * zeta)
    points: list[str] = []
    for i in range(samples + 1):
        t = duration * i / samples
        x = 1 - math.exp(-decay * t) * (math.cos(damped * t) + (decay / damped) * math.sin(damped * t))
        points.append(f"{x:.4f}".rstrip("0").rstrip(".") or "0")
    points[0] = "0"
    points[-1] = "1"
    return "linear(" + ", ".join(points) + ")", round(duration * 1000)


DARK_TOKENS = """
  color-scheme: dark;
  --bg: #0d0d10;
  --surface: rgba(34, 34, 38, 0.86);
  --surface-strong: rgba(44, 44, 49, 0.94);
  --surface-solid: #1c1c1e;
  --cell: rgba(255, 255, 255, 0.035);
  --sidebar: rgba(22, 22, 26, 0.66);
  --nav-selected: rgba(255, 255, 255, 0.10);
  --seg-thumb: #5a5a5f;
  --field: rgba(255, 255, 255, 0.06);
  --label: #f5f5f7;
  --label-2: #a1a1a6;
  --label-3: #6e6e73;
  --separator: rgba(84, 84, 88, 0.5);
  --hairline: rgba(255, 255, 255, 0.06);
  --glass-edge: rgba(255, 255, 255, 0.065);
  --glass-highlight: rgba(255, 255, 255, 0.05);
  --fill: rgba(120, 120, 128, 0.26);
  --fill-2: rgba(120, 120, 128, 0.15);
  --track: rgba(120, 120, 128, 0.22);
  --used: #636366;
  --check-border: rgba(235, 235, 245, 0.32);
  --shadow-color: rgba(0, 0, 0, 0.55);
  --shadow-color-sm: rgba(0, 0, 0, 0.35);
  --scrim: rgba(0, 0, 0, 0.5);
  --toast-bg: rgba(245, 245, 247, 0.94);
  --toast-fg: #1d1d1f;
  --blue: #0a84ff;
  --green: #30d158;
  --green-text: #30d158;
  --teal: #40c8e0;
  --orange: #ff9f0a;
  --orange-text: #ff9f0a;
  --red: #ff453a;
  --purple: #bf5af2;
  --indigo: #5e5ce6;
  --pink: #ff375f;
  --yellow: #ffd60a;
  --glow-strength: 34%;
  --glow-opacity: 0.55;
"""


TEMPLATE_HEAD = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>
@property --tint-a { syntax: "<color>"; inherits: false; initial-value: #0a84ff; }
@property --tint-b { syntax: "<color>"; inherits: false; initial-value: #40c8e0; }

:root {
  color-scheme: light;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue", "Microsoft YaHei", system-ui, sans-serif;
  --font-display: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;

  --bg: #f2f2f6;
  --surface: rgba(255, 255, 255, 0.84);
  --surface-strong: rgba(255, 255, 255, 0.94);
  --surface-solid: #ffffff;
  --cell: rgba(255, 255, 255, 0.62);
  --sidebar: rgba(240, 240, 245, 0.7);
  --nav-selected: rgba(255, 255, 255, 0.95);
  --seg-thumb: #ffffff;
  --field: rgba(255, 255, 255, 0.9);
  --label: #1d1d1f;
  --label-2: #6e6e73;
  --label-3: #a1a1a6;
  --separator: rgba(60, 60, 67, 0.14);
  --hairline: rgba(0, 0, 0, 0.06);
  --glass-edge: rgba(0, 0, 0, 0.055);
  --glass-highlight: rgba(255, 255, 255, 0.7);
  --fill: rgba(120, 120, 128, 0.14);
  --fill-2: rgba(120, 120, 128, 0.08);
  --track: rgba(120, 120, 128, 0.14);
  --used: #aeaeb2;
  --check-border: rgba(60, 60, 67, 0.3);
  --shadow-color: rgba(30, 30, 60, 0.12);
  --shadow-color-sm: rgba(0, 0, 0, 0.05);
  --scrim: rgba(20, 20, 30, 0.2);
  --toast-bg: rgba(29, 29, 31, 0.92);
  --toast-fg: #f5f5f7;
  --blue: #0071e3;
  --green: #34c759;
  --green-text: #1f8a3b;
  --teal: #30b0c7;
  --orange: #ff9500;
  --orange-text: #c45c00;
  --red: #ff3b30;
  --purple: #af52de;
  --indigo: #5856d6;
  --pink: #ff2d55;
  --yellow: #ffcc00;
  --glow-strength: 22%;
  --glow-opacity: 0.32;

  --ease-bounce: cubic-bezier(0.34, 1.32, 0.64, 1);
  --ease-soft: cubic-bezier(0.22, 1, 0.36, 1);
  --dur-bounce: __EASE_BOUNCE_MS__ms;
  --dur-soft: __EASE_SOFT_MS__ms;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --shadow-sm: 0 1px 2px var(--shadow-color-sm), 0 0 0 0.5px var(--hairline);
  --shadow-card: 0 1px 2px var(--shadow-color-sm), 0 16px 40px -18px var(--shadow-color);
  --shadow-float: 0 2px 6px var(--shadow-color-sm), 0 24px 60px -16px var(--shadow-color), inset 0 0.5px 0 var(--glass-highlight);
}
@supports (transition-timing-function: linear(0, 1)) {
  :root {
    --ease-bounce: __EASE_BOUNCE__;
    --ease-soft: __EASE_SOFT__;
  }
}
:root[data-theme="dark"] {__DARK_TOKENS__}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {__DARK_TOKENS__}
}

*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
html { -webkit-text-size-adjust: 100%; background: var(--bg); scrollbar-gutter: stable; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--label);
  font: 15px/1.5 var(--font);
  letter-spacing: -0.003em;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; -webkit-tap-highlight-color: transparent; }
input, select, textarea { font: inherit; color: inherit; }
:focus { outline: none; }
:focus-visible { outline: 3px solid color-mix(in srgb, var(--blue) 50%, transparent); outline-offset: 2px; border-radius: 8px; }
.num { font-variant-numeric: tabular-nums; }
.visually-hidden { position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.ico { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex: none; }

/* ---------- Ambient backdrop: tint follows the current section ---------- */
.backdrop {
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  --tint-a: #0a84ff; --tint-b: #40c8e0;
  background:
    radial-gradient(48vw 42vh at 18% -6%, color-mix(in srgb, var(--tint-a) var(--glow-strength), transparent), transparent 72%),
    radial-gradient(42vw 38vh at 92% 4%, color-mix(in srgb, var(--tint-b) var(--glow-strength), transparent), transparent 70%);
  transition: --tint-a 1.1s var(--ease-out), --tint-b 1.1s var(--ease-out);
}
[data-view="all"] .backdrop { --tint-a: #5e5ce6; --tint-b: #0a84ff; }
[data-view="mac"] .backdrop { --tint-a: #0a84ff; --tint-b: #5e5ce6; }
[data-view="projects"] .backdrop { --tint-a: #bf5af2; --tint-b: #ff375f; }
[data-view="github"] .backdrop { --tint-a: #5e5ce6; --tint-b: #8e8e93; }
[data-view="nas"] .backdrop { --tint-a: #30d158; --tint-b: #40c8e0; }
[data-view="whitelisted"] .backdrop { --tint-a: #ff9f0a; --tint-b: #ffd60a; }

/* ---------- App frame ---------- */
.app { position: relative; z-index: 1; display: grid; grid-template-columns: 252px minmax(0, 1fr); min-height: 100vh; }
.sidebar {
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
  display: flex; flex-direction: column; gap: 18px;
  padding: 22px 12px 16px;
  background: var(--sidebar);
  -webkit-backdrop-filter: blur(40px) saturate(190%);
  backdrop-filter: blur(40px) saturate(190%);
  border-right: 0.5px solid var(--separator);
}
.brand { display: flex; align-items: center; gap: 11px; padding: 0 8px; }
.brand-icon {
  width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; color: #fff; flex: none;
  background: conic-gradient(from 210deg, #64d2ff, #0a84ff, #5e5ce6, #30d158, #64d2ff);
  box-shadow: inset 0 0.5px 0 rgba(255, 255, 255, 0.55), 0 3px 10px -2px rgba(10, 132, 255, 0.45);
}
.brand-icon .ico { width: 20px; height: 20px; stroke-width: 2.2; }
.brand-name { font-weight: 650; font-size: 15px; letter-spacing: -0.012em; line-height: 1.25; }
.brand-sub { font-size: 12px; color: var(--label-2); line-height: 1.3; }
.nav { position: relative; display: flex; flex-direction: column; gap: 2px; }
.nav-pill {
  position: absolute; left: 0; right: 0; top: 0; height: 36px; border-radius: 10px;
  background: var(--nav-selected); box-shadow: var(--shadow-sm); will-change: transform, height; pointer-events: none;
}
.nav-item {
  position: relative; z-index: 1; display: flex; align-items: center; gap: 10px;
  height: 36px; padding: 0 10px 0 6px; border-radius: 10px; font-size: 14px; font-weight: 500; text-align: left;
  transition: background 0.15s, transform var(--dur-bounce) var(--ease-bounce);
}
.nav-item:hover:not(.active) { background: var(--fill-2); }
.nav-item:active { transform: scale(0.97); transition-duration: 70ms; }
.nav-count { margin-left: auto; font-size: 12.5px; color: var(--label-2); font-variant-numeric: tabular-nums; }
.nav-label { font-size: 12px; font-weight: 600; color: var(--label-3); padding: 14px 8px 4px; }
.sidebar-foot { margin-top: auto; display: flex; flex-direction: column; gap: 8px; padding: 0; }
.sidebar-foot .foot-label { font-size: 12px; color: var(--label-3); font-weight: 600; padding: 0 8px; }

.main { min-width: 0; padding: 0 44px 150px; }
.page { max-width: 1120px; margin: 0 auto; }
.page-head { padding: 46px 4px 22px; }
.large-title {
  margin: 0; font-family: var(--font-display); font-weight: 800;
  font-size: clamp(28px, 3.1vw, 38px); line-height: 1.12; letter-spacing: -0.02em;
  -webkit-text-stroke: 0.024em currentColor;
}
.page-sub { margin: 10px 0 0; max-width: 70ch; color: var(--label-2); font-size: 15px; line-height: 1.55; }
.page-sub b { color: var(--label); font-weight: 600; }

.glass {
  background: var(--surface);
  border: 0.5px solid var(--glass-edge);
  box-shadow: var(--shadow-card);
}

/* ---------- App icon tiles (System Settings style) ---------- */
.app-icon {
  flex: none; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center; color: #fff;
  background: linear-gradient(180deg, var(--c1), var(--c2));
  box-shadow: inset 0 0.5px 0 rgba(255, 255, 255, 0.4), 0 1px 2px rgba(0, 0, 0, 0.14);
}
.app-icon .ico { width: 16px; height: 16px; stroke-width: 2.1; }
.app-icon.sm { width: 24px; height: 24px; border-radius: 7px; }
.app-icon.sm .ico { width: 14px; height: 14px; }
.tint-blue { --c1: #47a3ff; --c2: #0a6cf0; }
.tint-green { --c1: #50d86c; --c2: #24b046; }
.tint-orange { --c1: #ffb54a; --c2: #ff8800; }
.tint-purple { --c1: #cb84ff; --c2: #9a46f0; }
.tint-teal { --c1: #5cd7e8; --c2: #17a9c2; }
.tint-indigo { --c1: #8582ff; --c2: #4d4ad6; }
.tint-graphite { --c1: #a7a7ad; --c2: #6c6c72; }
.tint-pink { --c1: #ff7393; --c2: #ff2d55; }
.tint-yellow { --c1: #ffd84d; --c2: #ffb300; }
.tint-rainbow { --c1: #64d2ff; --c2: #5e5ce6; }

/* ---------- Buttons & controls ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 30px; padding: 0 14px; border-radius: 999px; white-space: nowrap;
  font-size: 13.5px; font-weight: 560; background: var(--fill); color: var(--label);
  transition: transform var(--dur-bounce) var(--ease-bounce), background 0.15s, filter 0.15s;
}
.btn:hover { background: color-mix(in srgb, var(--fill) 100%, var(--fill-2)); filter: brightness(1.03); }
.btn:active { transform: scale(0.95); transition-duration: 70ms; }
.btn .ico { width: 15px; height: 15px; }
.btn-primary { background: var(--blue); color: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12), inset 0 0.5px 0 rgba(255, 255, 255, 0.3); }
.btn-primary:hover { background: var(--blue); filter: brightness(1.08); }
.btn-plain { background: transparent; color: var(--blue); }
.btn-plain:hover { background: var(--fill-2); }
.btn-small { height: 26px; padding: 0 11px; font-size: 12.5px; }
.btn-large { height: 40px; padding: 0 20px; font-size: 14.5px; }
.btn-warn { color: var(--orange-text); }

.seg { position: relative; display: inline-flex; padding: 2px; border-radius: 9px; background: var(--fill); isolation: isolate; max-width: 100%; overflow-x: auto; scrollbar-width: none; }
.seg::-webkit-scrollbar { display: none; }
.seg button {
  position: relative; z-index: 1; display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 13px; border-radius: 7px; font-size: 13px; font-weight: 520; white-space: nowrap;
  transition: transform var(--dur-bounce) var(--ease-bounce), color 0.2s;
}
.seg button:active { transform: scale(0.95); transition-duration: 70ms; }
.seg button .count { font-size: 12px; color: var(--label-2); font-variant-numeric: tabular-nums; }
.seg-glider { position: absolute; z-index: 0; top: 2px; bottom: 2px; left: 0; width: 0; border-radius: 7px; background: var(--seg-thumb); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.14), 0 0 0 0.5px rgba(0, 0, 0, 0.04); will-change: transform, width; }
.seg.small button { padding: 4px 10px; font-size: 12.5px; flex: 1; justify-content: center; }
.seg.small { display: flex; }

.popup { position: relative; display: inline-flex; align-items: center; }
.popup select {
  position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; pointer-events: none; opacity: 0;
}
.popup-btn {
  display: inline-flex; align-items: center; justify-content: space-between; gap: 8px;
  height: 30px; padding: 0 10px 0 12px; border-radius: 9px;
  border: 0.5px solid var(--separator); background: var(--field); font-size: 13px; font-weight: 540; color: var(--label);
  box-shadow: var(--shadow-sm); cursor: pointer; white-space: nowrap;
  transition: transform var(--dur-bounce) var(--ease-bounce), background 0.15s, border-color 0.15s, box-shadow 0.15s;
}
.popup-btn:hover { background: var(--surface-strong); }
.popup-btn:active { transform: scale(0.96); transition-duration: 70ms; }
.popup-btn:focus-visible, .popup-btn[aria-expanded="true"] { outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px color-mix(in srgb, var(--blue) 28%, transparent), var(--shadow-sm); }
.popup-btn .ico { position: static; width: 13px; height: 13px; color: var(--label-2); transition: transform var(--dur-bounce) var(--ease-bounce); }
.popup-btn[aria-expanded="true"] .ico { transform: rotate(180deg); color: var(--label); }
.popup > .ico { display: none; }
.popup-menu { min-width: 176px; padding: 5px; border-radius: 13px; }
.popup-menu button.menu-opt {
  display: flex; flex-direction: row; align-items: center; gap: 8px; width: 100%;
  padding: 6px 11px 6px 8px; border-radius: 8px; font-size: 13px; font-weight: 520; text-align: left; white-space: nowrap;
}
.popup-menu .opt-check { width: 14px; height: 14px; stroke: currentColor; stroke-width: 2.4; fill: none; stroke-linecap: round; stroke-linejoin: round; opacity: 0; flex: none; transition: opacity 0.15s; }
.popup-menu button.menu-opt[aria-selected="true"] .opt-check { opacity: 1; color: var(--blue); }
.popup-menu button.menu-opt:hover .opt-check, .popup-menu button.menu-opt:focus-visible .opt-check { color: #fff; }

/* ---------- Apple .contentTransition(.numericText()) per-digit spring animation ---------- */
.nt-wrap { display: inline-flex; align-items: baseline; font-variant-numeric: tabular-nums; }
.nt-ch { display: inline-block; }
.nt-slot { display: inline-grid; align-items: baseline; justify-items: center; position: relative; vertical-align: baseline; }
.nt-slot > .nt-old, .nt-slot > .nt-new {
  grid-area: 1 / 1; display: inline-block; backface-visibility: hidden; -webkit-backface-visibility: hidden;
  will-change: transform, opacity;
}
.nt-slot > .nt-old { pointer-events: none; user-select: none; }
.nt-slot.nt-exit, .nt-slot.nt-enter { overflow: visible; }

/* ---------- Volume strip (mini rings) ---------- */
.volume-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(172px, 1fr)); gap: 10px; margin-bottom: 22px; }
.vol {
  display: flex; align-items: center; gap: 11px; min-width: 0; padding: 10px 12px 10px 10px; border-radius: 16px; text-align: left;
  transition: transform var(--dur-bounce) var(--ease-bounce), box-shadow 0.25s var(--ease-out), background 0.2s;
}
.vol:hover { background: var(--surface-strong); }
.vol:active { transform: scale(0.97); transition-duration: 70ms; }
.vol[aria-selected="true"] { box-shadow: 0 0 0 2px var(--blue), var(--shadow-card); background: var(--surface-strong); }
.vol-ring-wrap { position: relative; width: 46px; height: 46px; flex: none; display: grid; place-items: center; }
.vol-ring { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.vol-text { min-width: 0; flex: 1; display: flex; flex-direction: column; }
.vol-name { font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vol-meta { font-size: 12px; color: var(--label-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.vol-meta.free { color: var(--green-text); font-weight: 560; }
.vol-pct { position: relative; font-size: 11px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.vol-pct.warn { color: var(--orange-text); }
.vol-pct.crit { color: var(--red); }

/* ---------- Hero: storage ring (Activity-ring style: thick, round caps) ---------- */
.hero { display: grid; grid-template-columns: minmax(250px, 330px) minmax(0, 1fr); gap: 44px; align-items: center; padding: 34px 40px; border-radius: 28px; }
.ring-stage { position: relative; width: 100%; max-width: 320px; aspect-ratio: 1; justify-self: center; }
.ring-canvas { display: block; width: 100%; height: 100%; }
.ring-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; pointer-events: none; padding: 0 20%; }
.ring-kicker { font-size: 13px; font-weight: 560; color: var(--label-2); max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ring-num { display: flex; align-items: baseline; gap: 5px; margin: 2px 0 4px; font-family: ui-rounded, "SF Pro Rounded", var(--font-display); font-variant-numeric: tabular-nums; }
.ring-num .n { font-size: clamp(38px, 4.4vw, 54px); font-weight: 750; letter-spacing: -0.035em; line-height: 1; transition: color 0.3s; }
.ring-num .u { font-size: 18px; font-weight: 650; color: var(--label-2); }
.ring-num.is-free .n { color: var(--green-text); }
.ring-num.is-focus .n { color: var(--blue); }
.ring-water { font-size: 13px; color: var(--label-2); font-variant-numeric: tabular-nums; }
.hero-info { min-width: 0; }
.hero-title-row { display: flex; align-items: center; gap: 10px; min-height: 34px; flex-wrap: nowrap; }
.hero-title { margin: 0; font-family: var(--font-display); font-size: 26px; font-weight: 800; letter-spacing: -0.018em; -webkit-text-stroke: 0.02em currentColor; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status-pill { flex: none; font-size: 12px; font-weight: 620; padding: 3px 10px; border-radius: 999px; color: var(--pill, var(--label-2)); background: color-mix(in srgb, var(--pill, var(--label-2)) 14%, transparent); }
.status-pill.ok { --pill: var(--green-text); }
.status-pill.warn { --pill: var(--orange-text); }
.status-pill.crit { --pill: var(--red); }
.hero-mount { margin: 6px 0 0; height: 20px; display: flex; align-items: center; gap: 8px; font-size: 13px; line-height: 20px; color: var(--label-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hero-mount code { font: 12px var(--font-mono); color: var(--label-3); overflow: hidden; text-overflow: ellipsis; }
.hero-sentence { margin: 12px 0 16px; height: 24px; font-size: 15px; line-height: 24px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hero-sentence b { font-weight: 650; }
.stat-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5px; margin: 0; border-radius: 14px; overflow: hidden; background: var(--separator); }
.stat { margin: 0; padding: 12px 14px; background: var(--cell); }
.stat dt { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--label-2); }
.stat dd { margin: 3px 0 0; font-size: 20px; font-weight: 650; letter-spacing: -0.018em; font-variant-numeric: tabular-nums; }
.stat dd.free { color: var(--green-text); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--dot, var(--used)); }
.dot.ring-used { --dot: var(--used); }
.dot.ring-clean { background: linear-gradient(135deg, var(--green), var(--teal)); }
.dot.ring-focus { --dot: var(--blue); }
.dot.ring-free { --dot: var(--track); box-shadow: inset 0 0 0 1px var(--separator); }

.comp { margin-top: 18px; }
.comp-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; height: 20px; margin-bottom: 8px; font-size: 13px; color: var(--label-2); }
.comp-head b { color: var(--label); font-weight: 620; font-size: 13.5px; }
/* Pill bar: every segment is its own capsule, matching the round-capped ring. */
.sbar { display: flex; gap: 3px; height: 14px; border-radius: 999px; overflow: hidden; background: var(--track); }
.sbar > i { display: block; height: 100%; flex: none; width: 0; min-width: 0; border-radius: 999px; transition: width var(--dur-bounce) var(--ease-bounce), opacity 0.2s; }
.sbar.lg { height: 20px; gap: 4px; }
.sbar.dim > i:not(.on) { opacity: 0.28; }
.comp-legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-auto-rows: 22px; gap: 6px 20px; margin-top: 12px; font-size: 12.5px; color: var(--label-2); }
#comp-legend { grid-template-rows: repeat(3, 22px); }
#impact-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 24px; min-height: 22px; }
.comp-legend > span { display: flex; align-items: center; gap: 7px; height: 22px; min-width: 0; cursor: default; transition: opacity 0.2s, transform var(--dur-soft) var(--ease-soft); }
.comp-legend > span.on { transform: translateX(2px); }
.comp-legend > span.is-empty { visibility: hidden; pointer-events: none; }
.comp-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.comp-legend b, .comp-legend em { flex: none; white-space: nowrap; }
.comp-legend b { color: var(--label); font-weight: 600; font-variant-numeric: tabular-nums; }
.comp-legend em { font-style: normal; color: var(--green-text); font-weight: 600; }
.comp-legend em:empty { display: none; }
.comp-legend.dim > span:not(.on) { opacity: 0.38; }

/* ---------- Sections & grouped lists ---------- */
.section { margin-top: 38px; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 0 4px 10px; }
.section-title { margin: 0; font-family: var(--font-display); font-size: 21px; font-weight: 800; letter-spacing: -0.014em; -webkit-text-stroke: 0.018em currentColor; }
.section-note { font-size: 13px; color: var(--label-2); }
.grouped { border-radius: 16px; overflow: hidden; }
.cell { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 11px 16px; text-align: left; width: 100%; transition: background 0.15s, transform 90ms var(--ease-out); }
.cell + .cell::before { content: ""; position: absolute; top: 0; left: 56px; right: 0; height: 0.5px; background: var(--separator); }
button.cell:hover, .cell.hoverable:hover { background: var(--fill-2); }
button.cell:active { background: var(--fill); transform: scale(0.996); }
.cell-text { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.cell-title { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 14.5px; font-weight: 520; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cell-title-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.cell-sub { font-size: 12.5px; line-height: 1.4; color: var(--label-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cell-sub code, .mono { font-family: var(--font-mono); font-size: 11.5px; }
.cell-right { text-align: right; font-variant-numeric: tabular-nums; display: flex; flex-direction: column; align-items: flex-end; justify-content: center; gap: 5px; }
.amount { font-size: 14.5px; font-weight: 620; line-height: 1.35; white-space: nowrap; }
.amount.free { color: var(--green-text); }
.amount.grow { color: var(--orange-text); }
.amount.muted { color: var(--label-2); font-weight: 520; font-size: 13px; }
.minibar { width: 92px; height: 4px; border-radius: 2px; background: var(--track); overflow: hidden; }
.minibar > i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--green), var(--teal)); transition: width var(--dur-bounce) var(--ease-bounce); }
.minibar.grow > i { background: linear-gradient(90deg, var(--orange), var(--pink)); }
.cell-more { justify-content: center; grid-template-columns: 1fr; color: var(--blue); font-size: 13.5px; font-weight: 560; text-align: center; }
.empty { padding: 26px 20px; text-align: center; color: var(--label-2); font-size: 14px; display: flex; flex-direction: column; align-items: center; gap: 12px; }
.empty b { color: var(--label); font-size: 15px; }
.diff-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; align-items: stretch; }
.diff-chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 0 4px 12px; }
.chip { flex: none; display: inline-flex; align-items: center; gap: 4px; height: 20px; padding: 0 8px; border-radius: 6px; font-size: 11.5px; font-weight: 620; white-space: nowrap; background: var(--fill-2); color: var(--label-2); }
.chip.lg { height: 26px; padding: 0 11px; border-radius: 999px; font-size: 12.5px; }
.chip.tier-a, .chip.good { color: var(--green-text); background: color-mix(in srgb, var(--green) 15%, transparent); }
.chip.tier-b, .chip.info { color: var(--blue); background: color-mix(in srgb, var(--blue) 13%, transparent); }
.chip.tier-c, .chip.warn { color: var(--orange-text); background: color-mix(in srgb, var(--orange) 15%, transparent); }

/* ---------- Workbench ---------- */
.impact { padding: 20px 22px; border-radius: 22px; margin-bottom: 14px; }
.impact-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 14px; }
.impact-title { margin: 0; font-size: 17px; font-weight: 650; letter-spacing: -0.012em; }
.impact-sub { margin: 2px 0 0; font-size: 13px; color: var(--label-2); font-variant-numeric: tabular-nums; }
.sbar-used { background: var(--used); }
.sbar-focus { background: var(--blue); }
.sbar-focus.preview { background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--blue) 70%, transparent) 0 5px, color-mix(in srgb, var(--blue) 40%, transparent) 5px 10px); }
.sbar-clean { background: linear-gradient(90deg, var(--green), var(--teal)); }
.toolbar { position: sticky; top: 0; z-index: 6; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 12px 0; margin-bottom: 8px; }
.toolbar::before {
  content: ""; position: absolute; inset: 0 -14px -12px; z-index: -1; pointer-events: none;
  background: color-mix(in srgb, var(--bg) 62%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(170%); backdrop-filter: blur(18px) saturate(170%);
  -webkit-mask-image: linear-gradient(#000 72%, transparent); mask-image: linear-gradient(#000 72%, transparent);
}
.toolbar-right { display: flex; align-items: center; gap: 8px; }
.list-group { margin-bottom: 26px; }
.group-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 0 4px 8px; }
.group-head h3 { margin: 0; font-size: 13.5px; font-weight: 620; color: var(--label-2); display: flex; align-items: center; gap: 8px; }
.group-head span { font-size: 12.5px; color: var(--label-3); font-variant-numeric: tabular-nums; }
.group-body { border-radius: 16px; overflow: hidden; }
.row { position: relative; display: grid; grid-template-columns: 50px minmax(0, 1fr); transition: background 0.18s; }
.row + .row::before { content: ""; position: absolute; top: 0; left: 90px; right: 0; height: 0.5px; background: var(--separator); }
.row:hover { background: var(--fill-2); }
.row.flash { animation: flash 1.2s var(--ease-out); }
@keyframes flash { 0%, 30% { background: color-mix(in srgb, var(--blue) 16%, transparent); } 100% { background: transparent; } }
.check { width: 50px; display: grid; place-items: center; align-self: stretch; }
.check:hover:not(:disabled) svg { box-shadow: inset 0 0 0 1.5px var(--blue); }
.check:active:not(:disabled) svg { transform: scale(0.9); }
.check svg {
  width: 22px; height: 22px; padding: 3px; border-radius: 50%; overflow: visible;
  fill: none; stroke: #fff; stroke-width: 2.8; stroke-linecap: round; stroke-linejoin: round;
  box-shadow: inset 0 0 0 1.5px var(--check-border); transition: background 0.18s, box-shadow 0.18s, transform 90ms var(--ease-out); will-change: transform;
}
.check svg path { stroke-dasharray: 22; stroke-dashoffset: 22; transition: stroke-dashoffset 0.28s var(--ease-out) 0.02s; }
.check[aria-checked="true"] svg { background: var(--green); box-shadow: inset 0 0 0 1.5px var(--green); }
.check[aria-checked="true"]:hover:not(:disabled) svg { filter: brightness(1.05); box-shadow: inset 0 0 0 1.5px var(--green); }
.check[aria-checked="true"] svg path { stroke-dashoffset: 0; }
.check:disabled { cursor: default; }
.row.is-done .check svg { background: var(--label-3); box-shadow: none; }
.row.is-done .check svg path { stroke-dashoffset: 0; }
.row.is-protected .check svg { box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--orange) 60%, transparent); }
.row-main { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 12px; padding: 11px 16px 11px 0; text-align: left; width: 100%; min-width: 0; }
.row-text { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.row-title { font-size: 14.5px; font-weight: 520; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row-sub { display: flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; font-size: 12px; color: var(--label-2); }
.row-sub .cat { overflow: hidden; text-overflow: ellipsis; }
.row-size { font-size: 14.5px; font-weight: 620; line-height: 1.35; white-space: nowrap; font-variant-numeric: tabular-nums; }
.row-size.badge { max-width: 170px; overflow: hidden; text-overflow: ellipsis; font-size: 13px; font-weight: 520; color: var(--label-2); }
.chev { color: var(--label-3); display: grid; place-items: center; width: 16px; height: 16px; transition: transform var(--dur-bounce) var(--ease-bounce), color 0.18s; }
.row-main:hover .chev { color: var(--label-2); }
.chev .ico { width: 14px; height: 14px; stroke-width: 2.2; }
.row-main[aria-expanded="true"] .chev { transform: rotate(90deg); color: var(--label); }
.row-detail { grid-column: 2; overflow: hidden; will-change: height; }
.row-detail-inner { padding: 2px 16px 16px 40px; transform-origin: top center; will-change: opacity, transform; }
.evidence { margin: 0 0 10px; max-width: 82ch; font-size: 13.5px; line-height: 1.6; color: var(--label-2); }
.path-line code { display: inline-block; max-width: 100%; padding: 3px 8px; border-radius: 6px; background: var(--fill-2); font: 12px/1.5 var(--font-mono); color: var(--label-2); word-break: break-all; }
.note-field { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; font-size: 12px; font-weight: 600; color: var(--label-2); }
.note-field input { padding: 8px 11px; border-radius: 9px; border: 0.5px solid var(--separator); background: var(--field); font-size: 13.5px; font-weight: 400; color: var(--label); transition: box-shadow 0.18s, border-color 0.18s; }
.note-field input:focus { border-color: var(--blue); box-shadow: 0 0 0 3.5px color-mix(in srgb, var(--blue) 30%, transparent); }
.detail-actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; font-size: 12.5px; color: var(--label-2); font-variant-numeric: tabular-nums; }

/* ---------- Dock, menu, sheet, toast ---------- */
.dock {
  position: fixed; z-index: 20; left: calc(50% + 126px); bottom: 22px; translate: -50% 0;
  display: flex; align-items: center; gap: 8px; padding: 8px 8px 8px 22px; border-radius: 999px;
  background: var(--surface-strong); box-shadow: var(--shadow-float);
  -webkit-backdrop-filter: blur(34px) saturate(190%); backdrop-filter: blur(34px) saturate(190%);
  border: 0.5px solid var(--glass-edge);
}
.dock-stat { display: flex; flex-direction: column; line-height: 1.22; min-width: 150px; margin-right: 8px; transform-origin: left center; will-change: transform; }
.dock-count { font-size: 12px; color: var(--label-2); font-variant-numeric: tabular-nums; }
.dock-count b { color: var(--label); font-weight: 620; }
.dock-bytes { font-size: 16.5px; font-weight: 650; letter-spacing: -0.012em; font-variant-numeric: tabular-nums; }
.dock-bytes b { color: var(--green-text); font-weight: 680; }
.menu {
  position: fixed; z-index: 30; min-width: 250px; padding: 6px; border-radius: 14px;
  background: var(--surface-strong); box-shadow: var(--shadow-float); border: 0.5px solid var(--glass-edge);
  -webkit-backdrop-filter: blur(34px) saturate(190%); backdrop-filter: blur(34px) saturate(190%);
  transform-origin: 50% 100%; will-change: transform, opacity;
}
.menu button { display: flex; flex-direction: column; align-items: flex-start; width: 100%; padding: 7px 11px; border-radius: 8px; font-size: 13.5px; font-weight: 520; text-align: left; }
.menu button small { font-size: 11.5px; font-weight: 400; color: var(--label-2); }
.menu button:hover, .menu button:focus-visible { background: var(--blue); color: #fff; outline: none; }
.menu button:hover small, .menu button:focus-visible small { color: rgba(255, 255, 255, 0.82); }
.menu hr { border: 0; height: 0.5px; margin: 5px 9px; background: var(--separator); }
.menu .is-armed { color: var(--orange-text); }
.sheet-backdrop { position: fixed; inset: 0; z-index: 40; display: grid; place-items: center; padding: 24px; background: var(--scrim); -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px); }
.sheet {
  width: min(660px, 100%); max-height: calc(100vh - 48px); display: flex; flex-direction: column; gap: 14px;
  padding: 24px; border-radius: 24px; background: var(--surface-solid);
  box-shadow: 0 30px 90px -20px rgba(0, 0, 0, 0.5), 0 0 0 0.5px var(--hairline); will-change: transform, opacity;
}
.sheet-head { display: flex; gap: 14px; align-items: flex-start; }
.sheet h2 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.016em; }
.sheet p { margin: 3px 0 0; font-size: 13.5px; color: var(--label-2); }
.sheet p.ok { color: var(--green-text); }
.sheet p.warn { color: var(--orange-text); }
.sheet textarea { flex: 1; min-height: 260px; resize: vertical; padding: 14px; border-radius: 12px; border: 0.5px solid var(--separator); background: var(--fill-2); font: 12.5px/1.65 var(--font-mono); color: var(--label); outline: none; }
.sheet footer { display: flex; justify-content: flex-end; gap: 8px; }
.toast {
  position: fixed; z-index: 50; left: 50%; bottom: 96px; translate: -50% 14px; opacity: 0; pointer-events: none;
  padding: 9px 16px; border-radius: 999px; background: var(--toast-bg); color: var(--toast-fg); font-size: 13px; font-weight: 560;
  box-shadow: 0 10px 30px -8px rgba(0, 0, 0, 0.35);
  transition: opacity 0.2s var(--ease-out), translate var(--dur-bounce) var(--ease-bounce);
}
.toast.show { opacity: 1; translate: -50% 0; }

/* One orchestrated entrance on first load only */
.enter { animation: rise var(--dur-bounce) var(--ease-bounce) both; animation-delay: calc(var(--i, 0) * 45ms); }
@keyframes rise { from { opacity: 0; transform: translateY(12px) scale(0.98); } to { opacity: 1; transform: none; } }

/* ---------- Responsive ---------- */
@media (max-width: 1100px) {
  .hero { grid-template-columns: 1fr; justify-items: center; gap: 28px; padding: 30px 26px; }
  .hero-info { width: 100%; }
  .diff-grid { grid-template-columns: 1fr; }
}
@media (max-width: 860px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { position: sticky; z-index: 15; height: auto; flex-direction: row; align-items: center; gap: 8px; padding: 10px 12px; border-right: 0; border-bottom: 0.5px solid var(--separator); overflow-x: auto; scrollbar-width: none; }
  .sidebar::-webkit-scrollbar { display: none; }
  .brand-text, .nav-label, .sidebar-foot, .nav-pill, .nav-count { display: none; }
  .brand { padding: 0; }
  .nav { flex-direction: row; gap: 4px; }
  .nav-item { white-space: nowrap; padding: 0 12px 0 6px; }
  .nav-item.active { background: var(--nav-selected); box-shadow: var(--shadow-sm); }
  .main { padding: 0 16px 160px; }
  .page-head { padding-top: 28px; }
  .dock { left: 12px; right: 12px; bottom: 12px; translate: none; border-radius: 22px; padding-left: 16px; }
  .dock-stat { min-width: 0; flex: 1; }
  .row-size.badge, .minibar { display: none; }
  .volume-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-grid { grid-template-columns: 1fr 1fr; }
  .toolbar { position: static; }
}
@media (max-width: 520px) {
  #quick-btn .label { display: none; }
  .row-detail-inner { padding-left: 0; }
}

/* ---------- Accessibility preferences ---------- */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 1ms !important; animation-delay: 0ms !important; transition-duration: 120ms !important; }
  .btn:active, .vol:active, .seg button:active, .nav-item:active { transform: none; }
}
@media (prefers-reduced-transparency: reduce) {
  .glass, .sidebar, .dock, .menu, .toolbar::before { -webkit-backdrop-filter: none; backdrop-filter: none; background: var(--surface-solid); }
  .backdrop { display: none; }
}
@media (prefers-contrast: more) {
  :root { --separator: rgba(60, 60, 67, 0.45); --glass-edge: rgba(0, 0, 0, 0.3); --label-2: #3a3a3c; }
  .glass, .dock, .menu { background: var(--surface-solid); }
}
@media print {
  .sidebar, .dock, .backdrop, .toolbar { display: none !important; }
  .app { display: block; }
  .page[hidden] { display: block !important; }
}
</style>
</head>
"""


TEMPLATE_BODY = r"""<body>
<div class="backdrop" aria-hidden="true"></div>
<div class="app">
  <aside class="sidebar" aria-label="侧栏">
    <div class="brand">
      <span class="brand-icon"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"/><path d="M12 7a5 5 0 1 0 5 5"/><path d="M12 12h9"/></svg></span>
      <div class="brand-text">
        <div class="brand-name">全栈巡检</div>
        <div class="brand-sub" id="brand-time" data-raw="__GENERATED_AT__">__GENERATED_AT__</div>
      </div>
    </div>
    <nav class="nav" id="nav" aria-label="页面导航">
      <span class="nav-pill" id="nav-pill" aria-hidden="true"></span>
      <button type="button" class="nav-item" data-view="overview"><span class="app-icon sm tint-rainbow" data-icon="gauge"></span>概览</button>
      <div class="nav-label">待审批</div>
      <button type="button" class="nav-item" data-view="all"><span class="app-icon sm tint-indigo" data-icon="list"></span>全部建议<span class="nav-count" id="cnt-all"></span></button>
      <button type="button" class="nav-item" data-view="mac"><span class="app-icon sm tint-blue" data-icon="laptop"></span>Mac 与备份<span class="nav-count" id="cnt-mac"></span></button>
      <button type="button" class="nav-item" data-view="projects"><span class="app-icon sm tint-purple" data-icon="folder"></span>本地项目<span class="nav-count" id="cnt-projects"></span></button>
      <button type="button" class="nav-item" data-view="github"><span class="app-icon sm tint-graphite" data-icon="branch"></span>GitHub<span class="nav-count" id="cnt-github"></span></button>
      <button type="button" class="nav-item" data-view="nas"><span class="app-icon sm tint-teal" data-icon="server"></span>NAS 与 PVE<span class="nav-count" id="cnt-nas"></span></button>
      <div class="nav-label">保护</div>
      <button type="button" class="nav-item" data-view="whitelisted"><span class="app-icon sm tint-orange" data-icon="shield"></span>白名单<span class="nav-count" id="cnt-whitelisted"></span></button>
    </nav>
    <div class="sidebar-foot">
      <span class="foot-label">外观</span>
      <div class="seg small" id="theme-seg" role="radiogroup" aria-label="外观">
        <span class="seg-glider" aria-hidden="true"></span>
        <button type="button" role="radio" data-value="auto">自动</button>
        <button type="button" role="radio" data-value="light">浅色</button>
        <button type="button" role="radio" data-value="dark">深色</button>
      </div>
    </div>
  </aside>

  <main class="main">
    <!-- 概览：大圆环 + 存储卷 + 基线变化 + 本次清单 -->
    <div class="page" id="ring-overview-page">
      <header class="page-head">
        <h1 class="large-title">__HEADING__</h1>
        <p class="page-sub" id="overview-sub"></p>
      </header>

      <div class="volume-strip" id="mini-rings-strip" role="tablist" aria-label="存储卷"></div>

      <section class="hero glass enter" id="hero" style="--i:2">
        <div class="ring-stage">
          <canvas class="ring-canvas" id="ring-canvas" width="320" height="320" role="img" aria-label="存储卷容量圆环：灰色为清理后已用，绿色为将释放，蓝色为悬停项"></canvas>
          <div class="ring-center">
            <span class="ring-kicker" id="ring-center-kicker">预计可释放</span>
            <div class="ring-num" id="ring-num"><span class="n" id="ring-center-num">0</span><span class="u" id="ring-center-unit">GiB</span></div>
            <span class="ring-water" id="ring-center-water"></span>
          </div>
        </div>
        <div class="hero-info">
          <div class="hero-title-row">
            <h2 class="hero-title" id="ring-active-title">存储卷</h2>
            <span class="status-pill" id="ring-active-badge"></span>
          </div>
          <p class="hero-mount" id="ring-active-mount"></p>
          <p class="hero-sentence" id="ring-active-desc"></p>
          <dl class="stat-grid">
            <div class="stat"><dt><i class="dot ring-used"></i>清理后已用</dt><dd id="rkpi-retained">0</dd></div>
            <div class="stat"><dt><i class="dot ring-clean"></i>将释放</dt><dd class="free" id="rkpi-cleaned">0</dd></div>
            <div class="stat"><dt><i class="dot ring-free"></i>清理后可用</dt><dd id="rkpi-free-after">0</dd></div>
            <div class="stat"><dt><i class="dot" style="--dot: var(--label-3)"></i>总容量</dt><dd id="rkpi-total">0</dd></div>
          </dl>
          <div class="comp" id="composition"></div>
        </div>
      </section>
      <div class="empty glass grouped" id="hero-empty" hidden><b>这份快照没有存储卷数据</b>下面的清单仍然可以勾选和导出。</div>

      <section class="section" id="diff-section" hidden>
        <div class="section-head">
          <h2 class="section-title">距上次巡检基线变化</h2>
          <span class="section-note" id="diff-time"></span>
        </div>
        <div class="diff-chips" id="diff-chips"></div>
        <div class="diff-grid">
          <div class="grouped glass" id="diff-gauges"></div>
          <div class="grouped glass" id="diff-growth"></div>
        </div>
      </section>

      <div id="ring-detailed-lists"></div>
    </div>

    <!-- 勾选与微调工作台 -->
    <div class="page" id="workbench-page" hidden>
      <header class="page-head">
        <h1 class="large-title" id="list-title">全部建议</h1>
        <p class="page-sub" id="list-sub"></p>
      </header>

      <section class="impact glass" id="impact-card">
        <div class="impact-head">
          <div>
            <h3 class="impact-title" id="impact-title">存储卷</h3>
            <p class="impact-sub" id="impact-sub"></p>
          </div>
          <label class="popup">
            <span class="visually-hidden">切换存储卷</span>
            <select id="impact-pool"></select>
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10l5 5 5-5"/></svg>
          </label>
        </div>
        <div class="sbar lg" aria-hidden="true">
          <i class="sbar-used" id="seg-retained"></i><i class="sbar-focus" id="seg-reclaim-item"></i><i class="sbar-clean" id="seg-reclaim-mid"></i>
        </div>
        <div class="comp-legend" id="impact-legend"></div>
      </section>

      <div class="toolbar">
        <div class="seg" id="action-seg" role="radiogroup" aria-label="按动作筛选">
          <span class="seg-glider" aria-hidden="true"></span>
          <button type="button" role="radio" data-value="all">全部 <span class="count" id="acnt-all"></span></button>
          <button type="button" role="radio" data-value="cleanup">空间清理 <span class="count" id="acnt-cleanup"></span></button>
          <button type="button" role="radio" data-value="organize">结构整理 <span class="count" id="acnt-organize"></span></button>
          <button type="button" role="radio" data-value="optimize">性能优化 <span class="count" id="acnt-optimize"></span></button>
          <button type="button" role="radio" data-value="update">版本更新 <span class="count" id="acnt-update"></span></button>
        </div>
        <div class="toolbar-right">
          <label class="popup">
            <span class="visually-hidden">排序方式</span>
            <select id="sort-select">
              <option value="default">默认顺序</option>
              <option value="size">按释放空间</option>
              <option value="pool">按存储卷</option>
              <option value="opt">优化和更新优先</option>
            </select>
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10l5 5 5-5"/></svg>
          </label>
          <button type="button" class="btn" id="select-visible-btn">全选本页</button>
        </div>
      </div>

      <div id="candidate-list"></div>
    </div>
  </main>
</div>

<div class="dock" id="dock" role="region" aria-label="已选项目">
  <div class="dock-stat" id="dock-stat">
    <span class="dock-count">已选 <b id="selected-count">0</b> 项</span>
    <span class="dock-bytes">将释放 <b id="selected-gib">0 GiB</b></span>
  </div>
  <button type="button" class="btn" id="quick-btn" aria-haspopup="menu" aria-expanded="false" aria-controls="quick-menu">
    <span class="label">快速选择</span><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 14l5-5 5 5"/></svg>
  </button>
  <button type="button" class="btn btn-primary btn-large" id="export-btn">生成执行指令</button>
</div>

<div class="menu" id="quick-menu" role="menu" aria-label="快速选择" hidden>
  <button type="button" role="menuitem" data-quick="tierA">只选 A 级<small>可安全重建，删了能自动恢复</small></button>
  <button type="button" role="menuitem" data-quick="tierAB">选 A 级和 B 级<small>加上建议整理的项目</small></button>
  <button type="button" role="menuitem" data-quick="opt">加选优化、整理和更新<small>不释放空间的维护项</small></button>
  <hr>
  <button type="button" role="menuitem" data-quick="clear">清空选择</button>
  <button type="button" role="menuitem" data-quick="reset" id="reset-item">恢复初始勾选<small>同时清掉备注和白名单改动</small></button>
</div>

<div class="sheet-backdrop" id="prompt-modal" hidden>
  <div class="sheet" id="prompt-sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <div class="sheet-head">
      <span class="app-icon tint-blue" data-icon="doc"></span>
      <div>
        <h2 id="sheet-title">执行指令已生成</h2>
        <p id="sheet-status">粘贴回对话框，Agent 会先跑 preflight 复查再动手。</p>
      </div>
    </div>
    <textarea id="prompt-textarea" readonly aria-label="执行指令文本"></textarea>
    <footer>
      <button type="button" class="btn" id="sheet-close">完成</button>
      <button type="button" class="btn btn-primary" id="copy-confirm-btn">复制指令</button>
    </footer>
  </div>
</div>

<div class="toast" id="toast" role="status" aria-live="polite"></div>
"""


TEMPLATE_SCRIPT_CORE = r"""<script>
"use strict";
const REPORT_DATA = __REPORT_JSON__;
const SPRING = __SPRING_PRESETS__;
const CANDS = Array.isArray(REPORT_DATA.candidates) ? REPORT_DATA.candidates : [];
const GAUGES = Array.isArray(REPORT_DATA.storage_gauges) ? REPORT_DATA.storage_gauges : [];
const STORAGE_KEY = "full_stack_maint_" + REPORT_DATA.report_id;
const THEME_KEY = "full_stack_maint_theme";
const motionQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
let REDUCED_MOTION = Boolean(motionQuery && motionQuery.matches);
if (motionQuery && motionQuery.addEventListener) motionQuery.addEventListener("change", e => { REDUCED_MOTION = e.matches; });

/* ================= Spring engine =================
 * Apple-style parameters: damping ratio (1 = no overshoot, < 1 = light rebound) and
 * response (seconds to arrive). Springs always continue from their current value and
 * velocity, so any animation can be interrupted and retargeted mid-flight. */
const springLoop = { active: new Set(), raf: 0, last: 0, inTick: false, jobs: new Set() };

function flushFrameJobs() {
  const jobs = Array.from(springLoop.jobs);
  springLoop.jobs.clear();
  jobs.forEach(fn => { try { fn(); } catch (e) {} });
}

function queueFrameJob(fn) {
  springLoop.jobs.add(fn);
  if (!springLoop.inTick) flushFrameJobs();
}

function springTick(ts) {
  const dt = springLoop.last ? Math.min(0.05, Math.max(0.001, (ts - springLoop.last) / 1000)) : 1 / 60;
  springLoop.last = ts;
  springLoop.inTick = true;
  springLoop.active.forEach(s => { if (!s.step(dt)) springLoop.active.delete(s); });
  springLoop.inTick = false;
  flushFrameJobs();
  if (springLoop.active.size) {
    springLoop.raf = requestAnimationFrame(springTick);
  } else {
    springLoop.raf = 0;
    springLoop.last = 0;
  }
}

class Spring {
  constructor(value, preset, onUpdate, options) {
    this.value = value;
    this.target = value;
    this.velocity = 0;
    this.onUpdate = onUpdate || null;
    this.onRest = null;
    this.precision = (options && options.precision) || 0.002;
    this.tolerance = (options && options.tolerance) || 0;
    this.configure(preset || SPRING.soft);
  }
  configure(preset) {
    this.damping = preset.damping;
    this.response = preset.response;
    return this;
  }
  to(target, preset) {
    if (preset) this.configure(preset);
    this.target = target;
    if (REDUCED_MOTION || !window.requestAnimationFrame) return this.jump(target);
    springLoop.active.add(this);
    if (!springLoop.raf) springLoop.raf = requestAnimationFrame(springTick);
    return this;
  }
  jump(value) {
    this.value = this.target = value;
    this.velocity = 0;
    springLoop.active.delete(this);
    if (this.onUpdate) this.onUpdate(value);
    if (this.onRest) this.onRest(value);
    return this;
  }
  step(dt) {
    const stiffness = Math.pow((2 * Math.PI) / this.response, 2);
    const friction = (4 * Math.PI * this.damping) / this.response;
    let remain = dt;
    while (remain > 1e-6) {
      const h = Math.min(1 / 240, remain);
      const accel = -stiffness * (this.value - this.target) - friction * this.velocity;
      this.velocity += accel * h;
      this.value += this.velocity * h;
      remain -= h;
    }
    const tol = this.tolerance || this.precision * Math.max(1, Math.abs(this.target));
    const settled = Math.abs(this.value - this.target) < tol && Math.abs(this.velocity) < tol * 10;
    if (settled) {
      this.value = this.target;
      this.velocity = 0;
    }
    if (this.onUpdate) this.onUpdate(this.value);
    if (settled && this.onRest) this.onRest(this.value);
    return !settled;
  }
}

/* A small "press-and-rebound" helper: start below 1 and spring back with light overshoot. */
function popScale(el, springStore, from) {
  if (!el || REDUCED_MOTION) return;
  let s = springStore.get(el);
  if (!s) {
    s = new Spring(1, SPRING.bounce, v => {
      el.style.transform = Math.abs(v - 1) < 0.001 && Math.abs(s.velocity) < 0.005 ? "" : "scale(" + v.toFixed(4) + ")";
    }, { tolerance: 0.001 });
    s.onRest = () => { el.style.transform = ""; };
    springStore.set(el, s);
  }
  s.value = Math.min(s.value, from);
  s.to(1, SPRING.bounce);
}
const popSprings = new WeakMap();

/* Apple SwiftUI .contentTransition(.numericText()) per-digit transition.
 * Verified against 60fps reference recordings (both increasing and decreasing):
 * Baseline-pinned (50% 86%) + cap-pinned (50% 18%) trajectories so digits never drop below the baseline
 * or collide with surrounding labels, paired with directional Gaussian blur dissolve/focus. */
const numericState = new WeakMap();
const NUMERIC_SLOT_DUR = 0.30;
const NUMERIC_STAGGER = 0.024;
function numericEase(u) {
  const x = clamp(u, 0, 1);
  return 0.5 - 0.5 * Math.cos(Math.PI * Math.pow(x, 0.84));
}
function splitNumericParts(str) {
  const m = String(str).match(/^([+\-−]?\d+(?:\.\d+)?)(.*)$/);
  if (!m) return null;
  const numStr = m[1];
  const suffix = m[2] || "";
  const dot = numStr.indexOf(".");
  return {
    intPart: dot >= 0 ? numStr.slice(0, dot) : numStr,
    decPart: dot >= 0 ? numStr.slice(dot) : "",
    suffix
  };
}
function parseNumericHint(str, hint) {
  if (typeof hint === "number" && !isNaN(hint)) return hint;
  const m = String(str).replace(/−/g, "-").match(/[+-]?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : 0;
}
function applyNumericSlotFrame(slot, dir, blurPeak) {
  const p = clamp(slot.v, 0, 1);
  const wCh = slot.wCh || 0.6;
  if (slot.isExit) {
    slot.slotEl.style.width = ((1 - p) * wCh).toFixed(3) + "em";
  } else if (slot.isEnter) {
    slot.slotEl.style.width = (p * wCh).toFixed(3) + "em";
  }
  // Decreasing (dir < 0): old shrinks uniformly onto bottom baseline (50% 86%) & dissolves into blur;
  //   new condenses from top cap-line (50% 16%, y = -0.22em -> 0) down onto the baseline.
  // Increasing (dir > 0): old shrinks uniformly toward top cap-line (50% 16%, y = 0 -> -0.22em) & dissolves;
  //   new grows uniformly up from bottom baseline (50% 86%).
  const up = dir > 0;
  if (slot.oldEl) {
    const q = clamp(p / 0.64, 0, 1);
    const scOld = (1 - 0.52 * q).toFixed(3);
    const yOld = up ? (-0.22 * q).toFixed(3) : (0.04 * q).toFixed(3);
    const opOld = q >= 1 ? "0" : clamp(Math.pow(1 - q, 0.90), 0, 1).toFixed(3);
    slot.oldEl.style.transformOrigin = up ? "50% 16%" : "50% 86%";
    slot.oldEl.style.transform = `translate3d(0,${yOld}em,0) scale(${scOld})`;
    slot.oldEl.style.opacity = opOld;
    if (blurPeak > 0) {
      const bOld = Math.pow(q, 0.95) * blurPeak;
      slot.oldEl.style.filter = bOld > 0.18 && q < 1 ? `blur(${bOld.toFixed(2)}px)` : "none";
    }
  }
  if (slot.newEl) {
    const r = clamp((p - 0.16) / 0.84, 0, 1);
    const rEase = 1 - Math.pow(1 - r, 1.65);
    const scNew = (0.48 + 0.52 * rEase).toFixed(3);
    const yNew = up ? (0.04 * (1 - rEase)).toFixed(3) : (-0.22 * (1 - rEase)).toFixed(3);
    const opNew = r <= 0 ? "0" : clamp(Math.pow(clamp(r / 0.46, 0, 1), 0.78), 0, 1).toFixed(3);
    slot.newEl.style.transformOrigin = up ? "50% 86%" : "50% 16%";
    slot.newEl.style.transform = `translate3d(0,${yNew}em,0) scale(${scNew})`;
    slot.newEl.style.opacity = opNew;
    if (blurPeak > 0) {
      const bNew = Math.pow(1 - r, 1.25) * blurPeak;
      slot.newEl.style.filter = bNew > 0.18 && r < 0.98 ? `blur(${bNew.toFixed(2)}px)` : "none";
    }
  }
}
function runSingleNumericStep(info, next, dir) {
  const el = info.el;
  const prev = info.shownText || info.text || next;
  info.shownText = next;
  info.dir = dir;
  info.elapsed = 0;
  const pA = splitNumericParts(prev);
  const pB = splitNumericParts(next);
  if (!pA || !pB) {
    springLoop.active.delete(info);
    info.slots = [];
    el.textContent = next;
    return;
  }
  const prevSlotByKey = {};
  if (springLoop.active.has(info) && info.slots.length) {
    info.slots.forEach(s => { if (s.key) prevSlotByKey[s.key] = s; });
  }
  const maxInt = Math.max(pA.intPart.length, pB.intPart.length);
  const iA = pA.intPart.padStart(maxInt, " ");
  const iB = pB.intPart.padStart(maxInt, " ");
  const maxDec = Math.max(pA.decPart.length, pB.decPart.length);
  const dA = pA.decPart.padEnd(maxDec, " ");
  const dB = pB.decPart.padEnd(maxDec, " ");
  const sA = iA + dA;
  const sB = iB + dB;
  const wrap = document.createElement("span");
  wrap.className = "nt-wrap";
  const slots = [];
  let stagger = 0;
  const addAnimSlot = (key, ca, cb, isExit) => {
    const isEnter = !ca && Boolean(cb);
    const slotEl = document.createElement("span");
    slotEl.className = "nt-slot is-anim" + (isExit ? " nt-exit" : (isEnter ? " nt-enter" : ""));
    let oldEl = null, newEl = null;
    const reused = prevSlotByKey[key];
    const keepFlight = reused && !reused.done && reused.cb === cb && reused.ca;
    let effCa = ca;
    let v0 = 0;
    if (keepFlight) {
      effCa = reused.ca;
      v0 = reused.v;
    } else if (reused && !reused.done && reused.v < 0.32 && reused.ca) {
      effCa = reused.ca;
    } else if (reused && !reused.done && reused.cb) {
      effCa = reused.cb;
    }
    if (effCa) {
      oldEl = document.createElement("span");
      oldEl.className = "nt-old";
      oldEl.setAttribute("aria-hidden", "true");
      oldEl.textContent = effCa;
      slotEl.appendChild(oldEl);
    }
    if (cb) {
      newEl = document.createElement("span");
      newEl.className = "nt-new";
      newEl.textContent = cb;
      slotEl.appendChild(newEl);
    }
    wrap.appendChild(slotEl);
    const refCh = cb || effCa || "0";
    const wCh = refCh === "." ? 0.28 : 0.60;
    const s = {
      key,
      ca: effCa,
      cb,
      slotEl,
      oldEl,
      newEl,
      isExit: Boolean(isExit),
      isEnter: Boolean(isEnter && !effCa),
      wCh,
      delay: keepFlight ? 0 : Math.min(0.14, stagger * NUMERIC_STAGGER),
      dur: keepFlight ? Math.max(0.12, NUMERIC_SLOT_DUR * (1 - v0 * 0.55)) : NUMERIC_SLOT_DUR,
      u: 0,
      v0,
      v: v0,
      done: false
    };
    stagger++;
    applyNumericSlotFrame(s, dir, info.blurPeak);
    slots.push(s);
  };
  for (let i = 0; i < sB.length; i++) {
    const ca = sA[i] === " " ? "" : sA[i];
    const cb = sB[i] === " " ? "" : sB[i];
    const key = "d" + (i - maxInt);
    const reused = prevSlotByKey[key];
    if (ca === cb && (!reused || reused.done)) {
      if (cb) {
        const ch = document.createElement("span");
        ch.className = "nt-ch";
        ch.textContent = cb;
        wrap.appendChild(ch);
      }
    } else if (!cb && ca) {
      addAnimSlot(key, ca, "", true);
    } else {
      addAnimSlot(key, ca, cb, false);
    }
  }
  if (pB.suffix) {
    if (pA.suffix === pB.suffix) {
      const ch = document.createElement("span");
      ch.className = "nt-ch";
      ch.textContent = pB.suffix;
      wrap.appendChild(ch);
    } else {
      addAnimSlot("suf", pA.suffix.trim(), pB.suffix, false);
    }
  }
  if (!slots.length) {
    springLoop.active.delete(info);
    info.slots = [];
    el.textContent = next;
    return;
  }
  el.replaceChildren(wrap);
  info.slots = slots;
  springLoop.active.add(info);
  if (!springLoop.raf) springLoop.raf = requestAnimationFrame(springTick);
}
function setNumericText(el, nextText, numericHint) {
  if (!el) return;
  const next = String(nextText == null ? "" : nextText);
  let info = numericState.get(el);
  const nextVal = parseNumericHint(next, numericHint);
  if (!info) {
    el.textContent = next;
    info = {
      el,
      text: next,
      shownText: next,
      val: nextVal,
      dir: 1,
      elapsed: 0,
      blurPeak: el.id === "ring-center-num" ? 9.5 : (el.id === "selected-gib" || (el.id && el.id.startsWith("rkpi-")) ? 3.8 : 2.2),
      slots: [],
      step(dt) {
        this.elapsed += dt;
        let activeCount = 0;
        for (let i = 0; i < this.slots.length; i++) {
          const s = this.slots[i];
          if (s.done) continue;
          if (this.elapsed <= s.delay) {
            activeCount++;
            continue;
          }
          const u = (this.elapsed - s.delay) / s.dur;
          if (u >= 1) {
            s.u = 1;
            s.v = 1;
            s.done = true;
          } else {
            s.u = u;
            s.v = s.v0 + (1 - s.v0) * numericEase(u);
            activeCount++;
          }
          applyNumericSlotFrame(s, this.dir, this.blurPeak);
        }
        if (activeCount === 0) {
          this.el.textContent = this.text;
          this.shownText = this.text;
          this.slots = [];
          return false;
        }
        return true;
      }
    };
    numericState.set(el, info);
    return;
  }
  if (info.text === next) {
    info.val = nextVal;
    return;
  }
  const baseText = info.shownText || info.text;
  const baseVal = info.val;
  info.text = next;
  info.val = nextVal;
  if (REDUCED_MOTION || !window.requestAnimationFrame) {
    springLoop.active.delete(info);
    info.slots = [];
    info.shownText = next;
    el.textContent = next;
    return;
  }
  const dir = nextVal > baseVal ? 1 : (nextVal < baseVal ? -1 : (next > baseText ? 1 : -1));
  runSingleNumericStep(info, next, dir);
}

/* ================= Storage & state ================= */
function safeStorageGet(key) {
  try { return window.localStorage ? window.localStorage.getItem(key) : null; } catch (e) { return null; }
}
function safeStorageSet(key, val) {
  try { if (window.localStorage) window.localStorage.setItem(key, val); } catch (e) {}
}
function safeStorageRemove(key) {
  try { if (window.localStorage) window.localStorage.removeItem(key); } catch (e) {}
}

function loadState() {
  try {
    const raw = safeStorageGet(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        return { checked: parsed.checked || {}, notes: parsed.notes || {}, whitelisted: parsed.whitelisted || {} };
      }
    }
  } catch (e) {}
  const checked = {};
  const whitelisted = {};
  CANDS.forEach(c => {
    if (c.whitelisted) whitelisted[c.id] = true;
    else if (c.default_checked != null ? c.default_checked : c.tier === "A") checked[c.id] = true;
  });
  return { checked, notes: {}, whitelisted };
}

let state = loadState();
function saveState() { safeStorageSet(STORAGE_KEY, JSON.stringify(state)); }

/* ================= Formatting ================= */
const KIB = 1024, MIB = 1024 ** 2, GIB = 1024 ** 3, TIB = 1024 ** 4;

function bytePair(bytes, digits) {
  const b = Math.abs(Number(bytes) || 0);
  const d = digits == null ? 2 : digits;
  if (b >= TIB) return { num: (b / TIB).toFixed(d), unit: "TiB" };
  if (b >= GIB) return { num: (b / GIB).toFixed(b / GIB >= 100 ? Math.min(d, 1) : d), unit: "GiB" };
  if (b >= MIB) return { num: (b / MIB).toFixed(b / MIB >= 100 ? 0 : 1), unit: "MiB" };
  if (b >= KIB) return { num: (b / KIB).toFixed(0), unit: "KiB" };
  return { num: "0", unit: "GiB" };
}
function formatBytes(bytes, digits) {
  const p = bytePair(bytes, digits);
  return p.num + " " + p.unit;
}
function formatShort(bytes) { return formatBytes(bytes, 1); }
function pct(part, total) { return total > 0 ? (part / total) * 100 : 0; }
function fmtPct(v) { return (Number(v) || 0).toFixed(1) + "%"; }
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function escapeHtml(str) {
  return String(str == null ? "" : str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function stripLeadingEmoji(str) {
  return String(str || "").replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\s]+/u, "").trim();
}
function displayTitle(c) {
  return stripLeadingEmoji(c.title || c.id).replace(/^【[^】]{1,10}】\s*/, "");
}
function formatTime(raw) {
  if (!raw) return "刚刚";
  const d = new Date(raw);
  if (isNaN(d.getTime())) return String(raw);
  try {
    return d.toLocaleString("zh-CN", { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    return String(raw);
  }
}

/* ================= Icons (SF Symbols-like strokes) ================= */
const ICONS = {
  gauge: '<path d="M4.5 17a8 8 0 1 1 15 0"/><path d="M12 13l3.5-4"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  laptop: '<rect x="5" y="5" width="14" height="10" rx="1.5"/><path d="M3 19h18"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  branch: '<circle cx="7" cy="6" r="2.2"/><circle cx="7" cy="18" r="2.2"/><circle cx="17" cy="7" r="2.2"/><path d="M7 8.2v7.6M17 9.2c0 4-4.5 3.8-8.6 7"/>',
  server: '<rect x="4" y="4.5" width="16" height="6" rx="1.6"/><rect x="4" y="13.5" width="16" height="6" rx="1.6"/><path d="M8 7.5h.01M8 16.5h.01"/>',
  shield: '<path d="M12 3.5l7 2.6v5.6c0 4.4-3 7.6-7 8.8-4-1.2-7-4.4-7-8.8V6.1z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
  trash: '<path d="M4.5 7h15M10 11v6M14 11v6"/><path d="M6.5 7l.8 11.2a2 2 0 0 0 2 1.8h5.4a2 2 0 0 0 2-1.8L17.5 7M9.5 7V4.5h5V7"/>',
  tray: '<path d="M4 13.5l2.2-7.2A2 2 0 0 1 8.1 5h7.8a2 2 0 0 1 1.9 1.3l2.2 7.2V18a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18z"/><path d="M4 13.5h4.5l1 2h5l1-2H20"/>',
  bolt: '<path d="M13 3L5.5 13.5H11l-1 7.5 7.5-10.5H12z"/>',
  arrowDown: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v8M8.5 12.5l3.5 3.5 3.5-3.5"/>',
  chevron: '<path d="M9.5 6l6 6-6 6"/>',
  doc: '<path d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M9 12h6M9 16h4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'
};
function icon(name) {
  return '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
}
function hydrateIcons(root) {
  (root || document).querySelectorAll("[data-icon]").forEach(el => {
    if (!el.firstChild) el.innerHTML = icon(el.getAttribute("data-icon"));
  });
}

/* ================= Candidate model ================= */
const DOMAIN_META = {
  all: { label: "全部建议" },
  mac: { label: "Mac 与备份" },
  projects: { label: "本地项目" },
  github: { label: "GitHub" },
  nas: { label: "NAS 与 PVE" },
  whitelisted: { label: "白名单" }
};
const ACTION_META = {
  cleanup: { label: "空间清理", icon: "trash", tint: "green", rank: 4 },
  organize: { label: "结构整理", icon: "tray", tint: "blue", rank: 3 },
  optimize: { label: "性能优化", icon: "bolt", tint: "orange", rank: 2 },
  update: { label: "版本更新", icon: "arrowDown", tint: "purple", rank: 1 }
};
const TIER_META = {
  A: { label: "A 级", desc: "可安全重建", cls: "tier-a" },
  B: { label: "B 级", desc: "建议整理", cls: "tier-b" },
  C: { label: "C 级", desc: "需要你拍板", cls: "tier-c" }
};
const COMP_COLORS = ["#0a84ff", "#bf5af2", "#ff9f0a", "#40c8e0", "#ff375f", "#5e5ce6", "#ffd60a", "#30d158"];

const candById = {};
CANDS.forEach(c => { candById[c.id] = c; });
const gaugeById = {};
GAUGES.forEach(g => { gaugeById[g.id] = g; });

function sizeOf(c) { return Math.max(0, Number(c.size_bytes) || 0); }
function isWl(c) { return Boolean(state.whitelisted[c.id]); }
function isDone(c) { return c.status === "done"; }
function isChecked(c) { return !isWl(c) && !isDone(c) && Boolean(state.checked[c.id]); }
function tierOf(c) { return TIER_META[c.tier] ? c.tier : "B"; }

function inferActionType(c) {
  if (c.action_type && ACTION_META[c.action_type]) return c.action_type;
  const text = `${c.id || ""} ${c.title || ""} ${c.category || ""}`.toLowerCase();
  const has = words => words.some(w => text.includes(w));
  if (has(["更新", "升级", "outdated", "upgrade", "rustup"])) return "update";
  if (has(["优化", "压缩", "pack", "fstrim", "规则", "drift", "修复", "ci_failure"])) return "optimize";
  if (has(["整理", "归档", "合入", "推送", "纳管", "规范", "软链", "governance", "unpushed", "unlinked"])) return "organize";
  return "cleanup";
}
function isOptOrUpdate(c) { return inferActionType(c) !== "cleanup"; }

function poolTitle(g) {
  if (!g) return "";
  const raw = stripLeadingEmoji(g.label || g.id);
  return raw.replace(/\s*[（(][^）)]*[）)]\s*/g, " ").trim() || String(g.id);
}
function warnPctOf(g) { return Number(g && (g.warn_used_pct || g.warn_pct)) || 85; }
function levelFor(pctValue, warn) {
  if (pctValue >= warn) return "crit";
  if (pctValue >= warn - 8) return "warn";
  return "ok";
}

function matchesView(c, view) {
  if (view === "whitelisted") return isWl(c);
  if (isWl(c)) return false;
  return view === "all" || c.domain === view;
}

/* Per-volume numbers used by the ring, the mini rings and the impact bar. */
function computePoolRingMetrics(g) {
  const total = Math.max(1, Number(g.total_bytes) || 1);
  const currentUsed = Math.max(0, Number(g.used_bytes) || 0);
  const currentFree = g.free_bytes != null ? Math.max(0, Number(g.free_bytes)) : Math.max(0, total - currentUsed);
  let doneBytes = 0;
  let checkedBytes = 0;
  let goalBytes = 0;
  CANDS.forEach(c => {
    if (c.gauge_id !== g.id || isWl(c)) return;
    const sz = sizeOf(c);
    if (!sz) return;
    goalBytes += sz;
    if (isDone(c)) doneBytes += sz;
    else if (state.checked[c.id]) checkedBytes += sz;
  });
  const explicitCleaned = Math.max(0, Number(g.cleaned_bytes) || 0);
  const hasDone = doneBytes > 0 || explicitCleaned > 0 || g.before_used_bytes != null;
  let retained, cleaned, beforeUsed, freeAfter, freeBefore;
  if (hasDone) {
    const base = doneBytes > 0 ? doneBytes : explicitCleaned;
    cleaned = Math.min(total, base + checkedBytes);
    retained = Math.max(0, Math.min(total - cleaned, currentUsed - checkedBytes));
    beforeUsed = Math.min(total, retained + cleaned);
    freeAfter = Math.max(0, total - retained);
    freeBefore = Math.max(0, total - beforeUsed);
  } else {
    cleaned = Math.min(currentUsed, checkedBytes);
    retained = Math.max(0, currentUsed - cleaned);
    beforeUsed = currentUsed;
    freeBefore = currentFree;
    freeAfter = currentFree + cleaned;
  }
  return {
    id: g.id,
    gauge: g,
    label: stripLeadingEmoji(g.label || g.id),
    shortTitle: poolTitle(g),
    mountPath: g.mount_path || "",
    total,
    retainedBytes: retained,
    cleanedBytes: cleaned,
    beforeUsedBytes: beforeUsed,
    freeBeforeBytes: freeBefore,
    freeAfterBytes: freeAfter,
    beforePct: Math.min(100, pct(beforeUsed, total)),
    afterPct: Math.min(100, pct(retained, total)),
    warnPct: warnPctOf(g),
    goalBytes: Math.max(goalBytes, cleaned),
    hasDone
  };
}

function computeAllMetrics() {
  const list = GAUGES.map(computePoolRingMetrics);
  const agg = list.reduce((acc, m) => {
    acc.total += m.total;
    acc.retainedBytes += m.retainedBytes;
    acc.cleanedBytes += m.cleanedBytes;
    acc.beforeUsedBytes += m.beforeUsedBytes;
    acc.freeBeforeBytes += m.freeBeforeBytes;
    acc.freeAfterBytes += m.freeAfterBytes;
    acc.goalBytes += m.goalBytes;
    acc.hasDone = acc.hasDone || m.hasDone;
    return acc;
  }, {
    id: "_all", gauge: null, label: "全部存储卷", shortTitle: "全部存储卷", mountPath: "",
    total: 0, retainedBytes: 0, cleanedBytes: 0, beforeUsedBytes: 0, freeBeforeBytes: 0, freeAfterBytes: 0,
    beforePct: 0, afterPct: 0, warnPct: 101, goalBytes: 0, hasDone: false
  });
  agg.mountPath = list.length + " 个存储卷合计";
  agg.beforePct = pct(agg.beforeUsedBytes, agg.total);
  agg.afterPct = pct(agg.retainedBytes, agg.total);
  return { list, agg };
}

function selectionSummary(filterFn) {
  let count = 0, bytes = 0;
  CANDS.forEach(c => {
    if (filterFn && !filterFn(c)) return;
    if (isChecked(c)) { count++; bytes += sizeOf(c); }
  });
  return { count, bytes };
}
"""


TEMPLATE_SCRIPT_OVERVIEW = r"""
/* ================= Storage ring =================
 * One thick ring in the style of the Apple Health activity ring: round caps, a gradient
 * that follows the arc, and each segment's rounded head resting on the previous one with
 * a soft shadow. Segments: retained used (grey), selected to free (green to teal) and
 * the hovered item (blue). Drawn on canvas; every value is driven by a spring. */
let activeRingPoolId = GAUGES.length ? GAUGES[0].id : "_all";
let hoveredRingItemId = null;
const BIG_RING = { box: 320, outer: 156, width: 32 };
const MINI_RING = { box: 46, outer: 22.5, width: 5 };
const ringPaintTheme = { dark: false, track: "rgba(120,120,128,0.14)", used: "#aeaeb2", green: "#34c759", teal: "#30b0c7", blue: "#0071e3" };

function refreshRingTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = name => cs.getPropertyValue(name).trim();
  ringPaintTheme.dark = v("color-scheme") === "dark";
  ringPaintTheme.track = v("--track") || ringPaintTheme.track;
  ringPaintTheme.used = v("--used") || ringPaintTheme.used;
  ringPaintTheme.green = v("--green") || ringPaintTheme.green;
  ringPaintTheme.teal = v("--teal") || ringPaintTheme.teal;
  ringPaintTheme.blue = v("--blue") || ringPaintTheme.blue;
}
function hexRgb(hex) {
  const h = String(hex).replace("#", "");
  if (h.length !== 6) return [52, 199, 89];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function rgba(hex, a) { const c = hexRgb(hex); return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`; }

/* segments: [{ frac, colors: [c1] | [c1, c2], glow }] laid end to end from 12 o'clock. */
function drawRing(canvas, segments, geom) {
  if (!canvas || !canvas.getContext) return;
  const cssSize = canvas.clientWidth || geom.box;
  const dpr = Math.min(3, window.devicePixelRatio || 1);
  const px = Math.max(1, Math.round(cssSize * dpr));
  if (canvas.width !== px) { canvas.width = px; canvas.height = px; }
  const ctx = canvas.getContext("2d");
  const k = px / geom.box;
  ctx.setTransform(k, 0, 0, k, 0, 0);
  ctx.clearRect(0, 0, geom.box, geom.box);
  const c = geom.box / 2;
  const w = geom.width;
  const r = geom.outer - w / 2;
  const TAU = Math.PI * 2;
  const start = -Math.PI / 2;
  const capAng = (w / 2) / r;
  ctx.lineCap = "round";
  ctx.lineWidth = w;
  ctx.shadowColor = "transparent";
  ctx.strokeStyle = ringPaintTheme.track;
  ctx.beginPath(); ctx.arc(c, c, r, 0, TAU); ctx.stroke();
  let offset = 0;
  segments.forEach(seg => {
    const frac = clamp(seg.frac, 0, 1 - offset);
    if (frac <= 0.0008) return;
    const a0 = start + offset * TAU;
    const a1 = a0 + frac * TAU;
    const c1 = seg.colors[0];
    const c2 = seg.colors[1] || c1;
    let stroke = c1;
    if (c2 !== c1 && ctx.createConicGradient) {
      const pad = Math.min(0.28, capAng * 1.6 + 0.08);
      const grad = ctx.createConicGradient(a0 - pad, c, c);
      const s0 = clamp((pad - capAng * 0.85) / TAU, 0, 0.25);
      const s1 = clamp((pad + frac * TAU + capAng * 0.95) / TAU, s0 + 0.02, 0.995);
      grad.addColorStop(0, c1);
      if (s0 > 0.002) grad.addColorStop(s0, c1);
      grad.addColorStop(s1, c2);
      grad.addColorStop(1, c2);
      stroke = grad;
    }
    // A segment that starts on top of another gets a soft shadow under its rounded head, clipped to the ring band.
    if (offset > 0.0008) {
      const fade = clamp(frac * 18, 0, 1);
      ctx.save();
      ctx.beginPath();
      ctx.arc(c, c, r + w / 2, 0, TAU);
      ctx.arc(c, c, Math.max(0, r - w / 2), 0, TAU, true);
      ctx.clip();
      ctx.globalAlpha = fade;
      ctx.shadowColor = ringPaintTheme.dark ? "rgba(0, 0, 0, 0.42)" : "rgba(0, 0, 0, 0.18)";
      ctx.shadowBlur = w * 0.26;
      ctx.fillStyle = c1;
      ctx.beginPath(); ctx.arc(c + r * Math.cos(a0), c + r * Math.sin(a0), w / 2 - 0.5, 0, TAU); ctx.fill();
      ctx.restore();
    }
    ctx.save();
    if (seg.glow) {
      ctx.shadowColor = rgba(c1, ringPaintTheme.dark ? 0.42 : 0.26);
      ctx.shadowBlur = w * 0.48;
    }
    ctx.strokeStyle = stroke;
    ctx.beginPath(); ctx.arc(c, c, r, a0, a1); ctx.stroke();
    ctx.restore();
    offset += frac;
  });
}

const ringFlags = { isFocus: false, hasFree: false };
const ringSprings = {
  ret: new Spring(0, SPRING.ring, () => queueFrameJob(paintRing), { tolerance: 0.0005 }),
  cln: new Spring(0, SPRING.ring, () => queueFrameJob(paintRing), { tolerance: 0.0005 }),
  foc: new Spring(0, SPRING.glide, () => queueFrameJob(paintRing), { tolerance: 0.0005 }),
  bytes: new Spring(0, SPRING.soft, null, { precision: 0.0004 })
};

function paintRing() {
  const t = ringPaintTheme;
  drawRing(document.getElementById("ring-canvas"), [
    { frac: ringSprings.ret.value, colors: [t.used] },
    { frac: ringSprings.cln.value, colors: [t.green, t.teal], glow: true },
    { frac: ringSprings.foc.value, colors: [t.blue] }
  ], BIG_RING);
  const wrap = document.getElementById("ring-num");
  if (wrap) {
    wrap.classList.toggle("is-focus", ringFlags.isFocus);
    wrap.classList.toggle("is-free", !ringFlags.isFocus && ringFlags.hasFree);
  }
}

/* Interruptible spring animation of the ring segments (fractions 0..1) and the centre number. */
function animateRingTo(retFrac, clnFrac, focFrac, displayBytes, isFocus) {
  ringFlags.isFocus = Boolean(isFocus);
  ringFlags.hasFree = displayBytes > 0;
  ringSprings.ret.to(clamp(retFrac, 0, 1), SPRING.ring);
  ringSprings.cln.to(clamp(clnFrac, 0, 1), SPRING.ring);
  ringSprings.foc.to(clamp(focFrac, 0, 1), isFocus ? SPRING.glide : SPRING.ring);
  ringSprings.bytes.jump(displayBytes);
  const pair = bytePair(Math.max(0, displayBytes));
  setNumericText(document.getElementById("ring-center-num"), pair.num, displayBytes);
  setNumericText(document.getElementById("ring-center-unit"), pair.unit, displayBytes);
  queueFrameJob(paintRing);
}

/* ================= Overview: mini rings ================= */
const miniSprings = {};
let miniBuilt = false;

function paintMini(id) {
  const sp = miniSprings[id];
  if (!sp) return;
  const t = ringPaintTheme;
  drawRing(document.getElementById("mring-canvas-" + id), [
    { frac: sp.ret.value, colors: [t.used] },
    { frac: sp.cln.value, colors: [t.green, t.teal] }
  ], MINI_RING);
}

function repaintAllRings() {
  refreshRingTheme();
  paintRing();
  Object.keys(miniSprings).forEach(paintMini);
}

function buildMiniRings(entries) {
  const strip = document.getElementById("mini-rings-strip");
  if (!strip) return;
  strip.innerHTML = entries.map((m, i) => `
    <button type="button" class="vol glass enter" role="tab" style="--i:${i}" id="mring-card-${escapeHtml(m.id)}" data-pool="${escapeHtml(m.id)}" aria-selected="false">
      <span class="vol-ring-wrap"><canvas class="vol-ring" id="mring-canvas-${escapeHtml(m.id)}" width="46" height="46" aria-hidden="true"></canvas><span class="vol-pct" id="mring-pct-${escapeHtml(m.id)}">0%</span></span>
      <span class="vol-text">
        <span class="vol-name" title="${escapeHtml(m.label)}">${escapeHtml(m.shortTitle)}</span>
        <span class="vol-meta" id="mring-meta-${escapeHtml(m.id)}"></span>
      </span>
    </button>`).join("");
  strip.querySelectorAll(".vol").forEach(btn => {
    btn.addEventListener("click", () => selectRingPool(btn.getAttribute("data-pool")));
  });
  entries.forEach(m => {
    const job = () => paintMini(m.id);
    miniSprings[m.id] = {
      ret: new Spring(0, SPRING.ring, () => queueFrameJob(job), { tolerance: 0.001 }),
      cln: new Spring(0, SPRING.ring, () => queueFrameJob(job), { tolerance: 0.001 })
    };
  });
  miniBuilt = true;
}

function updateMiniRings(entries, activeId) {
  if (!miniBuilt) buildMiniRings(entries);
  entries.forEach(m => {
    const card = document.getElementById("mring-card-" + m.id);
    if (card) card.setAttribute("aria-selected", String(m.id === activeId));
    const sp = miniSprings[m.id];
    if (sp) {
      sp.ret.to(m.retainedBytes / m.total);
      sp.cln.to(m.cleanedBytes / m.total);
    }
    const pctEl = document.getElementById("mring-pct-" + m.id);
    if (pctEl) {
      pctEl.textContent = m.afterPct.toFixed(0) + "%";
      pctEl.className = "vol-pct " + (m.id === "_all" ? "" : levelFor(m.afterPct, m.warnPct));
    }
    const metaEl = document.getElementById("mring-meta-" + m.id);
    if (metaEl) {
      if (m.cleanedBytes > 0) {
        metaEl.className = "vol-meta free";
        metaEl.textContent = (m.hasDone ? "已释放 " : "可释放 ") + formatShort(m.cleanedBytes);
      } else {
        metaEl.className = "vol-meta";
        metaEl.textContent = "可用 " + formatShort(m.freeAfterBytes) + "，共 " + formatShort(m.total);
      }
    }
  });
}

function selectRingPool(poolId) {
  if (hoverRingRaf) { cancelAnimationFrame(hoverRingRaf); hoverRingRaf = 0; }
  if (hoverClearTimer) { clearTimeout(hoverClearTimer); hoverClearTimer = 0; }
  pendingHoverRingId = null;
  activeRingPoolId = poolId;
  hoveredRingItemId = null;
  const card = document.getElementById("mring-card-" + poolId);
  popScale(card, popSprings, 0.96);
  renderRingOverview(false);
}

let hoverRingRaf = 0;
let hoverClearTimer = 0;
let pendingHoverRingId = null;
function highlightRingItem(itemId, active) {
  if (hoverClearTimer) { clearTimeout(hoverClearTimer); hoverClearTimer = 0; }
  if (!active) {
    hoverClearTimer = setTimeout(() => {
      hoverClearTimer = 0;
      pendingHoverRingId = null;
      if (hoveredRingItemId !== null) {
        hoveredRingItemId = null;
        renderRingOverview(true);
      }
    }, 45);
    return;
  }
  pendingHoverRingId = itemId;
  if (hoverRingRaf) return;
  hoverRingRaf = requestAnimationFrame(() => {
    hoverRingRaf = 0;
    if (hoveredRingItemId === pendingHoverRingId) return;
    hoveredRingItemId = pendingHoverRingId;
    renderRingOverview(true);
  });
}

/* ================= Overview: composition bar ================= */
const COMP_SLOTS = 6;
let compBuilt = false;

function compositionParts(active, metricsList) {
  let raw = [];
  if (active.id === "_all") {
    raw = metricsList.map((m, i) => ({
      key: "s" + i, name: m.shortTitle, bytes: m.beforeUsedBytes, color: COMP_COLORS[i % COMP_COLORS.length], free: m.cleanedBytes
    }));
  } else {
    const g = active.gauge;
    const parts = [];
    const freeByCat = {};
    CANDS.forEach(c => {
      if (c.gauge_id !== g.id || !isChecked(c)) return;
      const cat = c.category || "其他";
      freeByCat[cat] = (freeByCat[cat] || 0) + sizeOf(c);
    });
    if (Array.isArray(g.breakdown) && g.breakdown.length) {
      g.breakdown.forEach(b => parts.push({ name: String(b.name || b.label || "未命名"), bytes: Math.max(0, Number(b.size_bytes) || 0) }));
    } else {
      const byCat = {};
      CANDS.forEach(c => {
        if (c.gauge_id !== g.id || !sizeOf(c)) return;
        const cat = c.category || "其他";
        byCat[cat] = (byCat[cat] || 0) + sizeOf(c);
      });
      Object.keys(byCat).forEach(k => parts.push({ name: k, bytes: byCat[k] }));
    }
    parts.sort((a, b) => b.bytes - a.bytes);
    const known = parts.reduce((s, p) => s + p.bytes, 0);
    raw = parts.filter(p => p.bytes > 0).map((p, i) => ({
      key: "s" + i, name: p.name, bytes: p.bytes, color: COMP_COLORS[i % COMP_COLORS.length], free: freeByCat[p.name] || 0
    }));
    const rest = active.beforeUsedBytes - known;
    if (rest > active.total * 0.005) raw.push({ key: "rest", name: "其他已用", bytes: rest, color: "var(--used)", free: 0 });
    if (!raw.length && active.beforeUsedBytes > 0) {
      if (active.cleanedBytes > 0) {
        raw.push({ key: "s0", name: "清理后保留已用", bytes: active.retainedBytes, color: COMP_COLORS[0], free: 0 });
        raw.push({ key: "s1", name: "已选待释放", bytes: active.cleanedBytes, color: COMP_COLORS[7], free: active.cleanedBytes });
      } else {
        raw.push({ key: "s0", name: "当前已用空间", bytes: active.beforeUsedBytes, color: COMP_COLORS[0], free: 0 });
      }
    }
  }
  if (raw.length > COMP_SLOTS) {
    const head = raw.slice(0, COMP_SLOTS - 1);
    const tail = raw.slice(COMP_SLOTS - 1);
    head.push({
      key: "s" + (COMP_SLOTS - 1),
      name: "其他已用",
      bytes: tail.reduce((s, p) => s + p.bytes, 0),
      color: "var(--used)",
      free: tail.reduce((s, p) => s + (p.free || 0), 0)
    });
    return head;
  }
  return raw;
}

function renderComposition(active, metricsList) {
  const box = document.getElementById("composition");
  if (!box) return;
  const parts = compositionParts(active, metricsList);
  if (!compBuilt) {
    const slots = Array.from({ length: COMP_SLOTS }, (_, i) => i);
    box.innerHTML = `
      <div class="comp-head"><b id="comp-title">空间构成</b><span id="comp-sub"></span></div>
      <div class="sbar" id="comp-bar">${slots.map(i => `<i data-slot="${i}" style="display:none;width:0%"></i>`).join("")}</div>
      <div class="comp-legend" id="comp-legend">${slots.map(i => `<span data-slot="${i}" class="is-empty"><i class="dot"></i><span class="comp-name"></span><b></b><em></em></span>`).join("")}</div>`;
    const bar = box.querySelector("#comp-bar");
    const legend = box.querySelector("#comp-legend");
    legend.querySelectorAll("span[data-slot]").forEach(sp => {
      const slot = sp.getAttribute("data-slot");
      sp.addEventListener("mouseenter", () => {
        if (sp.classList.contains("is-empty")) return;
        bar.classList.add("dim"); legend.classList.add("dim");
        bar.querySelectorAll("i").forEach(i => i.classList.toggle("on", i.getAttribute("data-slot") === slot));
        legend.querySelectorAll("span[data-slot]").forEach(s => s.classList.toggle("on", s === sp));
      });
      sp.addEventListener("mouseleave", () => { bar.classList.remove("dim"); legend.classList.remove("dim"); });
    });
    compBuilt = true;
  }
  const titleEl = document.getElementById("comp-title");
  const subEl = document.getElementById("comp-sub");
  if (titleEl) titleEl.textContent = active.id === "_all" ? "各存储卷占用" : "空间构成";
  if (subEl) subEl.textContent = "清理前已用 " + formatShort(active.beforeUsedBytes);
  for (let i = 0; i < COMP_SLOTS; i++) {
    const p = parts[i];
    const seg = box.querySelector(`#comp-bar i[data-slot="${i}"]`);
    const lg = box.querySelector(`#comp-legend span[data-slot="${i}"]`);
    const w = p ? clamp(pct(p.bytes, active.total), 0, 100) : 0;
    if (seg) {
      if (p && w > 0.05) {
        seg.style.display = "block";
        seg.style.background = p.color;
        seg.style.width = w.toFixed(2) + "%";
      } else {
        seg.style.width = "0%";
        seg.style.display = "none";
      }
    }
    if (lg) {
      if (p) {
        lg.classList.remove("is-empty");
        lg.querySelector(".dot").style.background = p.color;
        const nameEl = lg.querySelector(".comp-name");
        nameEl.textContent = p.name;
        nameEl.title = p.name;
        lg.querySelector("b").textContent = formatShort(p.bytes);
        lg.querySelector("em").textContent = p.free > 0 ? "可释放 " + formatShort(p.free) : "";
      } else {
        lg.classList.add("is-empty");
        lg.querySelector(".comp-name").textContent = "";
        lg.querySelector("b").textContent = "";
        lg.querySelector("em").textContent = "";
      }
    }
  }
}

/* ================= Overview: render ================= */
function updateOverviewSub(agg) {
  const el = document.getElementById("overview-sub");
  if (!el) return;
  const total = CANDS.filter(c => !isWl(c)).length;
  const sel = selectionSummary();
  const parts = [`快照生成于 ${escapeHtml(formatTime(REPORT_DATA.generated_at))}。`];
  parts.push(`共 <b>${total}</b> 条建议，已选 <b>${sel.count}</b> 条`);
  parts.push(sel.bytes > 0 ? `，预计释放 <b>${formatBytes(sel.bytes)}</b>。` : "。");
  if (agg && agg.total > 0) parts.push(`所有存储卷清理后合计可用 <b>${formatBytes(agg.freeAfterBytes)}</b>。`);
  el.innerHTML = parts.join("");
}

function renderRingOverview(onlyUpdateArcs) {
  const { list, agg } = computeAllMetrics();
  if (!onlyUpdateArcs) updateOverviewSub(agg);
  const hero = document.getElementById("hero");
  const heroEmpty = document.getElementById("hero-empty");
  if (!list.length) {
    if (hero) hero.hidden = true;
    if (heroEmpty) heroEmpty.hidden = false;
    if (!onlyUpdateArcs) renderOverviewLists(null);
    return;
  }
  const entries = list.length > 1 ? list.concat([agg]) : list;
  const active = activeRingPoolId === "_all" ? agg : (list.find(m => m.id === activeRingPoolId) || list[0]);
  activeRingPoolId = active.id;
  if (!onlyUpdateArcs) updateMiniRings(entries, active.id);

  const total = Math.max(1, active.total);
  const retained = Math.min(total, active.retainedBytes);
  const cleaned = Math.min(total - retained, active.cleanedBytes);
  let focusBytes = 0;
  let focusFromRetained = false;
  let hovered = hoveredRingItemId ? candById[hoveredRingItemId] : null;
  if (hovered && (active.id === "_all" || hovered.gauge_id === active.id) && sizeOf(hovered) > 0) {
    if (isChecked(hovered) || isDone(hovered)) {
      focusBytes = Math.min(cleaned, sizeOf(hovered));
    } else if (isWl(hovered)) {
      focusBytes = Math.min(retained, sizeOf(hovered));
      focusFromRetained = focusBytes > 0;
    } else {
      hovered = null;
    }
  } else {
    hovered = null;
  }
  const retArc = focusFromRetained ? Math.max(0, retained - focusBytes) : retained;
  const other = focusFromRetained ? cleaned : Math.max(0, cleaned - focusBytes);
  animateRingTo(retArc / total, other / total, focusBytes / total, focusBytes > 0 ? focusBytes : cleaned, focusBytes > 0);

  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const setHtml = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  setText("ring-center-kicker", hovered ? displayTitle(hovered) : (active.hasDone ? "已释放" : "预计可释放"));
  setText("ring-center-water", cleaned > 0
    ? `水位 ${fmtPct(active.beforePct)} → ${fmtPct(active.afterPct)}`
    : `当前水位 ${fmtPct(active.afterPct)}`);
  if (onlyUpdateArcs) return;
  setText("ring-active-title", active.shortTitle);
  setHtml("ring-active-mount", active.id === "_all"
    ? `<code>${escapeHtml(active.mountPath)}</code><span>按各卷警戒线监控</span>`
    : `<code>${escapeHtml(active.mountPath || active.id)}</code><span>警戒线 ${fmtPct(active.warnPct)}</span>`);
  setNumericText(document.getElementById("rkpi-retained"), formatBytes(retained), retained);
  setNumericText(document.getElementById("rkpi-cleaned"), cleaned > 0 ? formatBytes(cleaned) : "0 GiB", cleaned);
  setNumericText(document.getElementById("rkpi-free-after"), formatBytes(active.freeAfterBytes), active.freeAfterBytes);
  setNumericText(document.getElementById("rkpi-total"), formatBytes(total), total);

  const badge = document.getElementById("ring-active-badge");
  const desc = document.getElementById("ring-active-desc");
  if (badge) {
    if (active.id === "_all") {
      const tight = list.filter(m => m.afterPct >= m.warnPct).length;
      badge.className = "status-pill " + (tight ? "warn" : "ok");
      badge.textContent = tight ? `${tight} 个卷清理后仍偏满` : "清理后都在警戒线以下";
    } else {
      const before = levelFor(active.beforePct, active.warnPct);
      const after = levelFor(active.afterPct, active.warnPct);
      badge.className = "status-pill " + after;
      badge.textContent = after === "crit" ? "空间紧张" : (before !== "ok" && after === "ok" ? "清理后恢复正常" : (after === "warn" ? "接近警戒线" : "状态良好"));
    }
  }
  if (desc) {
    const nSel = CANDS.filter(c => (active.id === "_all" || c.gauge_id === active.id) && isChecked(c) && sizeOf(c) > 0).length;
    if (cleaned > 0) {
      desc.innerHTML = `${active.hasDone ? "已经" : `勾选的 <b>${nSel}</b> 项会`}在${active.id === "_all" ? "所有存储卷上" : "这块盘上"}释放 <b>${formatBytes(cleaned)}</b>，水位从 <b>${fmtPct(active.beforePct)}</b> 降到 <b>${fmtPct(active.afterPct)}</b>。`;
    } else {
      desc.innerHTML = `${active.id === "_all" ? "所有存储卷" : "这块盘"}还没有选中要清理的项目，当前水位 <b>${fmtPct(active.afterPct)}</b>，警戒线 ${active.id === "_all" ? "按各卷设置" : fmtPct(active.warnPct)}。`;
    }
  }
  renderComposition(active, list);
  if (onlyUpdateArcs) return;
  renderOverviewLists(active);
}

/* ================= Overview: detail lists ================= */
const expandedOverviewGroups = new Set();

function overviewRow(c, opts) {
  const act = ACTION_META[inferActionType(c)];
  const g = gaugeById[c.gauge_id];
  const subParts = [];
  if (g) subParts.push(escapeHtml(poolTitle(g)));
  if (c.path) subParts.push(`<code>${escapeHtml(c.path)}</code>`);
  let right = "";
  if (opts.kind === "space") {
    const sz = sizeOf(c);
    right = `<span class="amount free">${formatBytes(sz)}</span><span class="minibar"><i style="width:${Math.max(4, pct(sz, opts.max)).toFixed(1)}%"></i></span>`;
  } else if (opts.kind === "protected") {
    right = `<span class="amount muted">${sizeOf(c) > 0 ? "保留 " + formatBytes(sizeOf(c)) : "已锁定"}</span>`;
  } else {
    right = `<span class="amount muted">${escapeHtml(c.metric_badge || (isDone(c) ? "已完成" : "待执行"))}</span>`;
  }
  const tile = opts.kind === "protected" ? '<span class="app-icon tint-orange">' + icon("shield") + "</span>" : `<span class="app-icon tint-${act.tint}">${icon(act.icon)}</span>`;
  return `<button type="button" class="cell" data-id="${escapeHtml(c.id)}">
      ${tile}
      <span class="cell-text"><span class="cell-title">${escapeHtml(displayTitle(c))}</span><span class="cell-sub">${subParts.join("，")}</span></span>
      <span class="cell-right">${right}</span>
    </button>`;
}

function overviewGroup(key, title, note, items, opts) {
  const limit = 6;
  const open = expandedOverviewGroups.has(key);
  const shown = open ? items : items.slice(0, limit);
  const more = items.length > limit
    ? `<button type="button" class="cell cell-more" data-more="${key}">${open ? "收起" : `显示全部 ${items.length} 项`}</button>`
    : "";
  return `<section class="section">
      <div class="section-head"><h2 class="section-title">${title}</h2><span class="section-note">${note}</span></div>
      <div class="grouped glass">${shown.map(c => overviewRow(c, opts)).join("")}${more}</div>
    </section>`;
}

function overviewEmptyGroup(title, note, iconName, tint, mainText, subText, actionLabel) {
  const right = actionLabel ? `<span class="amount" style="color:var(--blue)">${actionLabel}</span>` : `<span class="amount muted">0 项</span>`;
  const tag = actionLabel ? `button type="button" data-goto="all"` : `div`;
  const closeTag = actionLabel ? `button` : `div`;
  return `<section class="section">
      <div class="section-head"><h2 class="section-title">${title}</h2><span class="section-note">${note}</span></div>
      <div class="grouped glass">
        <${tag} class="cell">
          <span class="app-icon tint-${tint}">${icon(iconName)}</span>
          <span class="cell-text"><span class="cell-title">${mainText}</span><span class="cell-sub">${subText}</span></span>
          <span class="cell-right">${right}</span>
        </${closeTag}>
      </div>
    </section>`;
}

function renderOverviewLists(active) {
  const box = document.getElementById("ring-detailed-lists");
  if (!box) return;
  const inPool = c => !active || active.id === "_all" ? true : c.gauge_id === active.id;
  const scope = CANDS.filter(inPool);
  const space = scope.filter(c => !isWl(c) && (isDone(c) || state.checked[c.id]) && sizeOf(c) > 0).sort((a, b) => sizeOf(b) - sizeOf(a));
  const opt = scope.filter(c => !isWl(c) && (isDone(c) || state.checked[c.id]) && sizeOf(c) === 0);
  const prot = scope.filter(c => isWl(c));
  const spaceSum = space.reduce((s, c) => s + sizeOf(c), 0);
  const max = Math.max(1, ...space.map(sizeOf));
  const where = active && active.id !== "_all" ? active.shortTitle : "所有存储卷";
  let out = "";
  if (space.length) {
    out += overviewGroup("space", "将释放空间", `${where}，${space.length} 项，共 ${formatBytes(spaceSum)}`, space, { kind: "space", max });
  } else {
    out += overviewEmptyGroup("将释放空间", `${where}，0 项`, "trash", "green", `${escapeHtml(where)}还没有选中会释放空间的项目`, "去全部建议里勾选后，环形图与水位会实时变化", "去全部建议 →");
  }
  if (opt.length) {
    out += overviewGroup("opt", "优化与整理", `${where}，${opt.length} 项，不改变磁盘占用`, opt, { kind: "opt" });
  } else {
    out += overviewEmptyGroup("优化与整理", `${where}，0 项`, "bolt", "orange", `${escapeHtml(where)}没有待执行的优化与整理项`, "结构整理、版本更新与配置核对会归在这里", "");
  }
  if (prot.length) {
    out += overviewGroup("prot", "受保护", `${where}，${prot.length} 项，执行时一律跳过`, prot, { kind: "protected" });
  } else {
    out += overviewEmptyGroup("受保护", `${where}，0 项`, "shield", "orange", `${escapeHtml(where)}没有受白名单保护的条目`, "在任意建议详情里点「加入白名单」即可锁定保护", "");
  }
  box.innerHTML = out;
  box.querySelectorAll(".cell[data-id]").forEach(cell => {
    const id = cell.getAttribute("data-id");
    cell.addEventListener("mouseenter", () => highlightRingItem(id, true));
    cell.addEventListener("mouseleave", () => highlightRingItem(id, false));
    cell.addEventListener("focus", () => highlightRingItem(id, true));
    cell.addEventListener("blur", () => highlightRingItem(id, false));
    cell.addEventListener("click", () => openItem(id));
  });
  box.querySelectorAll("[data-more]").forEach(btn => btn.addEventListener("click", () => {
    const k = btn.getAttribute("data-more");
    if (expandedOverviewGroups.has(k)) expandedOverviewGroups.delete(k); else expandedOverviewGroups.add(k);
    renderOverviewLists(active);
    const nextBtn = box.querySelector(`[data-more="${k}"]`);
    if (nextBtn) popScale(nextBtn, popSprings, 0.96);
  }));
  box.querySelectorAll("[data-goto]").forEach(btn => btn.addEventListener("click", () => navigate(btn.getAttribute("data-goto"))));
}

/* ================= Baseline diff ================= */
function pathName(p) {
  const clean = String(p || "").replace(/\s*[（(].*$/, "").replace(/\/+$/, "");
  return clean.split("/").filter(Boolean).pop() || String(p || "");
}
function renderDiff() {
  const diff = REPORT_DATA.diff;
  const sec = document.getElementById("diff-section");
  if (!sec) return;
  const gd = diff && Array.isArray(diff.gauge_deltas) ? diff.gauge_deltas : [];
  const gp = diff && Array.isArray(diff.top_growth_paths) ? diff.top_growth_paths : [];
  if (!gd.length && !gp.length) { sec.hidden = true; return; }
  sec.hidden = false;
  const t = document.getElementById("diff-time");
  if (t) t.textContent = `${diff.before_generated_at ? formatTime(diff.before_generated_at) : "上次快照"} 到 ${diff.after_generated_at ? formatTime(diff.after_generated_at) : "这次快照"}`;
  const chips = document.getElementById("diff-chips");
  if (chips) {
    const nNew = (diff.new_candidate_ids || []).length;
    const nGone = (diff.resolved_candidate_ids || []).length;
    chips.innerHTML = (nNew ? `<span class="chip lg warn">新出现 ${nNew} 条建议</span>` : "") + (nGone ? `<span class="chip lg good">已消除 ${nGone} 条</span>` : "");
  }
  const maxG = Math.max(1, ...gd.map(x => Math.abs(Number(x.used_delta_bytes) || 0)));
  const gBox = document.getElementById("diff-gauges");
  if (gBox) {
    gBox.innerHTML = gd.length ? gd.map(x => {
      const d = Number(x.used_delta_bytes) || 0;
      const grow = d > 0;
      return `<div class="cell hoverable">
          <span class="app-icon tint-${grow ? "orange" : "green"}">${icon("gauge")}</span>
          <span class="cell-text"><span class="cell-title"><span class="cell-title-text">${escapeHtml(poolTitle(gaugeById[x.id] || { id: x.id, label: x.label || x.id }))}</span></span>
            <span class="cell-sub">已用空间${grow ? "增加" : (d < 0 ? "减少" : "没有变化")}</span></span>
          <span class="cell-right"><span class="amount ${grow ? "grow" : "free"}">${grow ? "+" : (d < 0 ? "−" : "")}${formatBytes(Math.abs(d))}</span>
            <span class="minibar ${grow ? "grow" : ""}"><i style="width:${Math.max(4, pct(Math.abs(d), maxG)).toFixed(1)}%"></i></span></span>
        </div>`;
    }).join("") : `<div class="empty">没有存储卷变化数据</div>`;
  }
  const maxP = Math.max(1, ...gp.map(x => Math.abs(Number(x.delta_bytes) || 0)));
  const pBox = document.getElementById("diff-growth");
  if (pBox) {
    pBox.innerHTML = gp.length ? gp.slice(0, 6).map(x => {
      const d = Number(x.delta_bytes) || 0;
      return `<div class="cell hoverable" title="${escapeHtml(x.path)}">
          <span class="app-icon tint-${x.is_new ? "pink" : "indigo"}">${icon("folder")}</span>
          <span class="cell-text"><span class="cell-title"><span class="cell-title-text">${escapeHtml(pathName(x.path))}</span>${x.is_new ? '<span class="chip warn">新出现</span>' : ""}</span>
            <span class="cell-sub"><code>${escapeHtml(x.path)}</code></span></span>
          <span class="cell-right"><span class="amount grow">+${formatBytes(Math.abs(d))}</span>
            <span class="minibar grow"><i style="width:${Math.max(4, pct(Math.abs(d), maxP)).toFixed(1)}%"></i></span></span>
        </div>`;
    }).join("") : `<div class="empty">没有目录增长数据</div>`;
  }
}
"""


TEMPLATE_SCRIPT_WORKBENCH = r"""
/* ================= Navigation ================= */
const VIEWS = ["overview", "all", "mac", "projects", "github", "nas", "whitelisted"];
let currentView = "overview";
let currentAction = "all";
let currentSort = "default";
let activePoolId = GAUGES.length ? GAUGES[0].id : "";
let focusedItemId = null;
let lastToggledId = null;

const navPill = {
  y: new Spring(0, SPRING.glide, v => { const el = document.getElementById("nav-pill"); if (el) el.style.transform = "translateY(" + v.toFixed(2) + "px)"; }, { tolerance: 0.2 }),
  h: new Spring(36, SPRING.glide, v => { const el = document.getElementById("nav-pill"); if (el) el.style.height = v.toFixed(2) + "px"; }, { tolerance: 0.2 }),
  placed: false
};
function placeNavPill(animate) {
  const btn = document.querySelector(`.nav-item[data-view="${currentView}"]`);
  if (!btn || !btn.offsetHeight) return;
  if (animate && navPill.placed) { navPill.y.to(btn.offsetTop, SPRING.glide); navPill.h.to(btn.offsetHeight, SPRING.glide); }
  else { navPill.y.jump(btn.offsetTop); navPill.h.jump(btn.offsetHeight); }
  navPill.placed = true;
}

const pageSpring = new Spring(1, SPRING.soft, v => {
  const page = document.getElementById(currentView === "overview" ? "ring-overview-page" : "workbench-page");
  if (!page) return;
  page.style.opacity = v >= 0.999 ? "" : clamp(v, 0, 1).toFixed(3);
  page.style.transform = v >= 0.999 ? "" : "translateY(" + ((1 - v) * 14).toFixed(2) + "px)";
});

function navigate(view, opts) {
  if (VIEWS.indexOf(view) < 0) view = "overview";
  const changed = view !== currentView;
  currentView = view;
  if (typeof closeAllPopups === "function") closeAllPopups(null);
  if (typeof menuOpen !== "undefined" && menuOpen) toggleQuickMenu(false);
  document.documentElement.setAttribute("data-view", view);
  document.querySelectorAll(".nav-item").forEach(b => {
    const on = b.getAttribute("data-view") === view;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  placeNavPill(true);
  const isOverview = view === "overview";
  document.getElementById("ring-overview-page").hidden = !isOverview;
  document.getElementById("workbench-page").hidden = isOverview;
  if (isOverview) {
    renderRingOverview(false);
  } else {
    if (changed) { currentAction = "all"; pickPoolForView(); }
    renderCandidates();
    if (actionSeg) actionSeg.place(currentAction, false);
  }
  try { history.replaceState(null, "", "#" + view); } catch (e) {}
  if (changed && !(opts && opts.keepScroll)) window.scrollTo({ top: 0 });
  if (changed) { pageSpring.value = 0; pageSpring.to(1, SPRING.soft); }
}

/* Old entry point kept for compatibility with earlier exports and docs. */
function switchMainView(mode) { navigate(mode === "workbench" ? "all" : "overview"); }

function pickPoolForView() {
  const firstVisible = CANDS.find(c => matchesView(c, currentView) && c.gauge_id && gaugeById[c.gauge_id] && tierOf(c) === "A")
    || CANDS.find(c => matchesView(c, currentView) && c.gauge_id && gaugeById[c.gauge_id]);
  if (firstVisible && currentView !== "all") {
    activePoolId = firstVisible.gauge_id;
    return;
  }
  const byPool = {};
  CANDS.forEach(c => {
    if (!c.gauge_id || !gaugeById[c.gauge_id] || !matchesView(c, currentView)) return;
    byPool[c.gauge_id] = (byPool[c.gauge_id] || 0) + (tierOf(c) === "A" ? 1e15 : 0) + (isChecked(c) ? sizeOf(c) : 0) + 1;
  });
  const best = Object.keys(byPool).sort((a, b) => byPool[b] - byPool[a])[0];
  if (best) activePoolId = best;
}

/* ================= Segmented controls ================= */
function setupSegmented(el, initial, onChange) {
  if (!el) return null;
  const glider = el.querySelector(".seg-glider");
  const sx = new Spring(0, SPRING.glide, v => { glider.style.transform = "translateX(" + v.toFixed(2) + "px)"; }, { tolerance: 0.2 });
  const sw = new Spring(0, SPRING.glide, v => { glider.style.width = Math.max(0, v).toFixed(2) + "px"; }, { tolerance: 0.2 });
  let placed = false;
  let current = initial;
  function place(value, animate) {
    current = value;
    let target = null;
    el.querySelectorAll("button[data-value]").forEach(b => {
      const on = b.getAttribute("data-value") === value;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
      if (on) target = b;
    });
    if (!target || !target.offsetWidth) { glider.style.opacity = "0"; placed = false; return; }
    glider.style.opacity = "1";
    if (animate && placed) { sx.to(target.offsetLeft, SPRING.glide); sw.to(target.offsetWidth, SPRING.glide); }
    else { sx.jump(target.offsetLeft); sw.jump(target.offsetWidth); }
    placed = true;
  }
  el.addEventListener("click", e => {
    const b = e.target.closest("button[data-value]");
    if (!b) return;
    const val = b.getAttribute("data-value");
    const changed = val !== current;
    place(val, true);
    if (changed) onChange(val);
  });
  place(initial, false);
  return { place, refresh: animate => place(current, Boolean(animate && placed)) };
}
let actionSeg = null;
let themeSeg = null;

/* ================= Theme ================= */
function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
  try { repaintAllRings(); } catch (e) {}
}
function setTheme(theme) {
  const t = theme === "light" || theme === "dark" ? theme : "auto";
  safeStorageSet(THEME_KEY, t);
  applyTheme(t);
  if (themeSeg) themeSeg.place(t, true);
}
function initTheme() {
  const saved = safeStorageGet(THEME_KEY);
  applyTheme(saved);
  return saved === "light" || saved === "dark" ? saved : "auto";
}

/* ================= Workbench list ================= */
const rowSprings = new Map();
const expandedRows = new Set();
const listSpring = new Spring(1, SPRING.soft, v => {
  const list = document.getElementById("candidate-list");
  if (!list) return;
  list.style.opacity = v >= 0.999 ? "" : clamp(v, 0, 1).toFixed(3);
  list.style.transform = v >= 0.999 ? "" : "translateY(" + ((1 - v) * 8).toFixed(2) + "px)";
});

function animateListTransition() {
  if (REDUCED_MOTION) return;
  listSpring.value = 0.25;
  listSpring.velocity = 0;
  listSpring.to(1, SPRING.soft);
}

function sortItems(items) {
  const bySize = (a, b) => sizeOf(b) - sizeOf(a);
  if (currentSort === "size") items.sort(bySize);
  else if (currentSort === "pool") items.sort((a, b) => String(a.gauge_id || "~").localeCompare(String(b.gauge_id || "~")) || bySize(a, b));
  else if (currentSort === "opt") items.sort((a, b) => ACTION_META[inferActionType(a)].rank - ACTION_META[inferActionType(b)].rank || bySize(a, b));
  return items;
}

function visibleItems() {
  return CANDS.filter(c => matchesView(c, currentView) && (currentAction === "all" || inferActionType(c) === currentAction));
}

function impactText(c) {
  const g = gaugeById[c.gauge_id];
  const sz = sizeOf(c);
  if (!g || !sz) return sz ? `大小 ${formatBytes(sz)}` : "不改变磁盘占用";
  const m = computePoolRingMetrics(g);
  const share = pct(sz, m.total);
  if (isChecked(c)) return `占 ${poolTitle(g)} ${fmtPct(share)}，已算进清理后水位 ${fmtPct(m.afterPct)}`;
  return `占 ${poolTitle(g)} ${fmtPct(share)}，勾选后水位 ${fmtPct(m.afterPct)} → ${fmtPct(Math.max(0, m.afterPct - share))}`;
}

function rowHtml(c) {
  const act = ACTION_META[inferActionType(c)];
  const tier = TIER_META[tierOf(c)];
  const wl = isWl(c), done = isDone(c), checked = isChecked(c);
  const sz = sizeOf(c);
  const g = gaugeById[c.gauge_id];
  const id = escapeHtml(c.id);
  const title = escapeHtml(displayTitle(c));
  const note = state.notes[c.id] != null ? state.notes[c.id] : (c.whitelist_note || "");
  const sizeHtml = sz > 0
    ? `<span class="row-size">${formatBytes(sz)}</span>`
    : `<span class="row-size badge">${escapeHtml(c.metric_badge || "")}</span>`;
  const statusChip = done ? '<span class="chip good">已执行</span>' : (wl ? '<span class="chip warn">受保护</span>' : "");
  return `<div class="row${checked ? " is-checked" : ""}${wl ? " is-protected" : ""}${done ? " is-done" : ""}" id="card-${id}" data-id="${id}">
    <button type="button" class="check" role="checkbox" aria-checked="${checked || done}" aria-label="选择：${title}" data-act="toggle"${wl || done ? " disabled" : ""}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 12.5l3.8 3.8 7.2-8.3"/></svg>
    </button>
    <button type="button" class="row-main" aria-expanded="false" aria-controls="detail-${id}" data-act="expand">
      <span class="app-icon tint-${wl ? "orange" : act.tint}">${icon(wl ? "shield" : act.icon)}</span>
      <span class="row-text">
        <span class="row-title" title="${title}">${title}</span>
        <span class="row-sub"><span class="chip ${tier.cls}">${tier.label} ${tier.desc}</span>${statusChip}${g ? `<span class="chip">${escapeHtml(poolTitle(g))}</span>` : ""}<span class="cat">${escapeHtml(c.category || act.label)}</span></span>
      </span>
      ${sizeHtml}
      <span class="chev">${icon("chevron")}</span>
    </button>
    <div class="row-detail" id="detail-${id}" hidden><div class="row-detail-inner">
      ${c.evidence ? `<p class="evidence">${escapeHtml(c.evidence)}</p>` : ""}
      ${c.path ? `<div class="path-line"><code>${escapeHtml(c.path)}</code></div>` : ""}
      <label class="note-field">微调备注
        <input type="text" data-act="note" value="${escapeHtml(note)}" placeholder="比如：只保留最近 2 个版本，或者先移到 NAS 再删">
      </label>
      <div class="detail-actions">
        <span id="cimpact-${id}">${escapeHtml(impactText(c))}</span>
        <button type="button" class="btn btn-small${wl ? "" : " btn-warn"}" data-act="wl">${wl ? "移出白名单" : "加入白名单"}</button>
      </div>
    </div></div>
  </div>`;
}

function renderCandidates() {
  const list = document.getElementById("candidate-list");
  if (!list) return;
  const items = sortItems(visibleItems());
  const selBtn = document.getElementById("select-visible-btn");
  if (selBtn) selBtn.hidden = currentView === "whitelisted";
  const titleEl = document.getElementById("list-title");
  if (titleEl) titleEl.textContent = DOMAIN_META[currentView] ? DOMAIN_META[currentView].label : "全部建议";
  if (!items.length) {
    list.innerHTML = `<div class="grouped glass empty"><b>${currentView === "whitelisted" ? "白名单是空的" : "这里没有待处理的建议"}</b>${currentView === "whitelisted" ? "在任意建议的详情里点「加入白名单」，它就会出现在这里，执行时一律跳过。" : "换一个分类看看，或者回到概览。"}</div>`;
  } else {
    const groups = currentView === "whitelisted" ? [["W", items]] : ["A", "B", "C"].map(t => [t, items.filter(c => tierOf(c) === t)]).filter(x => x[1].length);
    list.innerHTML = groups.map(([t, arr]) => {
      const head = t === "W" ? "受保护的项目" : `${TIER_META[t].label}：${TIER_META[t].desc}`;
      return `<section class="list-group" data-tier="${t}">
        <header class="group-head"><h3><span class="chip ${t === "W" ? "warn" : TIER_META[t].cls}">${t === "W" ? "白名单" : t}</span>${head}</h3><span id="ghead-${t}"></span></header>
        <div class="group-body glass">${arr.map(rowHtml).join("")}</div>
      </section>`;
    }).join("");
    expandedRows.forEach(id => { if (document.getElementById("detail-" + id)) setExpanded(id, true, true); });
  }
  updateSelectionTotals();
}

function setExpanded(id, open, instant) {
  const detail = document.getElementById("detail-" + id);
  const card = document.getElementById("card-" + id);
  if (!detail || !card) return;
  const inner = detail.querySelector(".row-detail-inner");
  const btn = card.querySelector(".row-main");
  if (btn) btn.setAttribute("aria-expanded", String(open));
  if (open) expandedRows.add(id); else expandedRows.delete(id);
  let s = rowSprings.get(id);
  if (!s || !document.body.contains(s.el)) {
    if (s) springLoop.active.delete(s);
    s = new Spring(0, SPRING.soft, v => {
      const h = Math.max(0, v);
      detail.style.height = h.toFixed(2) + "px";
      if (inner && s.span > 0) {
        const p = clamp(h / s.span, 0, 1);
        inner.style.opacity = p >= 0.995 ? "" : p.toFixed(3);
        inner.style.transform = p >= 0.995 ? "" : "translateY(" + ((1 - p) * -4).toFixed(2) + "px)";
      }
    }, { tolerance: 0.5 });
    s.el = detail;
    s.span = 1;
    rowSprings.set(id, s);
  }
  const ClearInner = () => { if (inner) { inner.style.opacity = ""; inner.style.transform = ""; } };
  if (open) {
    const inFlight = springLoop.active.has(s) && !detail.hidden;
    const startH = detail.hidden ? 0 : (inFlight ? s.value : detail.offsetHeight);
    detail.hidden = false;
    detail.style.height = "auto";
    const target = Math.max(1, detail.scrollHeight);
    s.span = target;
    detail.style.height = startH.toFixed(2) + "px";
    if (!inFlight) s.value = startH;
    s.onRest = () => { if (expandedRows.has(id)) { detail.style.height = "auto"; ClearInner(); } };
    if (instant) { s.jump(target); detail.style.height = "auto"; ClearInner(); }
    else s.to(target, SPRING.soft);
  } else {
    const inFlight = springLoop.active.has(s) && !detail.hidden;
    const startH = inFlight ? s.value : detail.offsetHeight;
    s.span = Math.max(1, s.span || startH || 1);
    if (!inFlight) s.value = startH;
    s.onRest = () => { if (!expandedRows.has(id)) { detail.hidden = true; detail.style.height = ""; ClearInner(); } };
    if (instant) { s.jump(0); detail.hidden = true; detail.style.height = ""; ClearInner(); }
    else s.to(0, SPRING.soft);
  }
}

function updateRow(c, pop) {
  const card = document.getElementById("card-" + c.id);
  if (!card) return;
  const checked = isChecked(c);
  card.classList.toggle("is-checked", checked);
  const chk = card.querySelector(".check");
  if (chk) {
    chk.setAttribute("aria-checked", String(checked || isDone(c)));
    if (pop) popScale(chk.querySelector("svg"), popSprings, checked ? 0.72 : 0.86);
  }
  const imp = document.getElementById("cimpact-" + c.id);
  if (imp) imp.textContent = impactText(c);
}

function toggleItem(id, checked) {
  const c = candById[id];
  if (!c || isWl(c) || isDone(c)) return;
  state.checked[id] = checked == null ? !state.checked[id] : Boolean(checked);
  saveState();
  lastToggledId = id;
  if (state.checked[id] && c.gauge_id && gaugeById[c.gauge_id]) activePoolId = c.gauge_id;
  updateRow(c, true);
  updateSelectionTotals(true);
}

function updateNote(id, val) {
  state.notes[id] = val;
  saveState();
}

function toggleWhitelist(id) {
  const c = candById[id];
  if (!c) return;
  state.whitelisted[id] = !state.whitelisted[id];
  if (state.whitelisted[id]) state.checked[id] = false;
  saveState();
  showToast(state.whitelisted[id] ? "已加入白名单，导出时会一并写明" : "已移出白名单");
  updateSelectionTotals(true);
  const card = document.getElementById("card-" + id);
  const leaving = card && !matchesView(c, currentView);
  if (leaving && !REDUCED_MOTION) {
    const h = card.offsetHeight;
    card.style.overflow = "hidden";
    const s = new Spring(h, SPRING.soft, v => { card.style.height = Math.max(0, v) + "px"; card.style.opacity = clamp(v / h, 0, 1).toFixed(3); }, { tolerance: 0.5 });
    s.onRest = () => { expandedRows.delete(id); renderCandidates(); };
    s.to(0, SPRING.soft);
  } else {
    renderCandidates();
  }
}

function selectVisibleCategory() {
  let n = 0;
  visibleItems().forEach(c => { if (!isWl(c) && !isDone(c) && !state.checked[c.id]) { state.checked[c.id] = true; n++; } });
  saveState();
  renderCandidates();
  showToast(n ? `本页又选中了 ${n} 项` : "本页已经全选了");
}
function selectByTier(tiers) {
  const set = new Set(tiers);
  CANDS.forEach(c => { if (!isDone(c) && !isWl(c)) state.checked[c.id] = set.has(tierOf(c)); });
  saveState();
  refreshAfterBulk("已按风险等级重新勾选");
}
function selectOptimizations() {
  CANDS.forEach(c => { if (!isDone(c) && !isWl(c) && isOptOrUpdate(c)) state.checked[c.id] = true; });
  saveState();
  refreshAfterBulk("已加选优化、整理和更新");
}
function clearSelections() {
  state.checked = {};
  saveState();
  refreshAfterBulk("已清空选择");
}
function resetSavedState() {
  safeStorageRemove(STORAGE_KEY);
  state = loadState();
  expandedRows.clear();
  refreshAfterBulk("已恢复到这份报告的初始勾选");
}
function refreshAfterBulk(message) {
  if (currentView === "overview") updateSelectionTotals(true); else renderCandidates();
  popScale(document.getElementById("dock-stat"), popSprings, 0.9);
  if (message) showToast(message);
}

/* ================= Impact bar (workbench) ================= */
const impactSprings = {};
["seg-retained", "seg-reclaim-item", "seg-reclaim-mid"].forEach(id => {
  impactSprings[id] = new Spring(0, SPRING.ring, v => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.width = clamp(v, 0, 100).toFixed(3) + "%";
    el.style.display = v < 0.05 ? "none" : "";  // keep the pill gap only between visible segments
  }, { tolerance: 0.02 });
});

function updateImpact() {
  const card = document.getElementById("impact-card");
  if (!card) return;
  const g = gaugeById[activePoolId];
  if (!g || currentView === "whitelisted") { card.hidden = true; return; }
  card.hidden = false;
  const sel = document.getElementById("impact-pool");
  if (sel && sel.value !== g.id) { sel.value = g.id; if (poolPopup) poolPopup.sync(); }
  const m = computePoolRingMetrics(g);
  const total = m.total;
  const saved = m.cleanedBytes;
  let focus = focusedItemId ? candById[focusedItemId] : null;
  if (!focus || focus.gauge_id !== g.id || isWl(focus) || !sizeOf(focus)) focus = null;
  if (!focus && lastToggledId) {
    const lt = candById[lastToggledId];
    if (lt && lt.gauge_id === g.id && isChecked(lt) && sizeOf(lt)) focus = lt;
  }
  const preview = Boolean(focus && !isChecked(focus) && !isDone(focus));
  const focusBytes = focus ? Math.min(preview ? m.retainedBytes : saved, sizeOf(focus)) : 0;
  const retained = Math.max(0, m.retainedBytes - (preview ? focusBytes : 0));
  const otherSaved = Math.max(0, saved - (preview ? 0 : focusBytes));
  impactSprings["seg-retained"].to(pct(retained, total));
  impactSprings["seg-reclaim-item"].to(pct(focusBytes, total));
  impactSprings["seg-reclaim-mid"].to(pct(otherSaved, total));
  const focusEl = document.getElementById("seg-reclaim-item");
  if (focusEl) focusEl.classList.toggle("preview", preview);
  const t = document.getElementById("impact-title");
  if (t) t.textContent = poolTitle(g);
  const sub = document.getElementById("impact-sub");
  if (sub) {
    sub.textContent = saved > 0
      ? `清理后水位 ${fmtPct(m.beforePct)} → ${fmtPct(m.afterPct)}，可用空间变成 ${formatBytes(m.freeAfterBytes)}`
      : `当前水位 ${fmtPct(m.afterPct)}，可用 ${formatBytes(m.freeAfterBytes)}`;
  }
  const lg = document.getElementById("impact-legend");
  if (lg) {
    const focusLabel = focus ? (preview ? "这一项（未勾选）" : "这一项") : "";
    lg.innerHTML = `<span><i class="dot ring-used"></i>清理后已用 <b>${formatBytes(retained)}</b></span>` +
      (focus ? `<span><i class="dot ring-focus"></i>${focusLabel} <b>${formatBytes(focusBytes)}</b></span>` : "") +
      `<span><i class="dot ring-clean"></i>${focus && !preview ? "其他已选" : "将释放"} <b>${formatBytes(otherSaved)}</b></span>` +
      `<span><i class="dot ring-free"></i>原本可用 <b>${formatBytes(Math.max(0, total - m.beforeUsedBytes))}</b></span>`;
  }
}

/* ================= Totals, counters, dock ================= */
const dockBytes = new Spring(0, SPRING.soft, null, { precision: 0.0004 });

function updateCounts() {
  const counts = { all: 0, mac: 0, projects: 0, github: 0, nas: 0, whitelisted: 0 };
  const acts = { all: 0, cleanup: 0, organize: 0, optimize: 0, update: 0 };
  CANDS.forEach(c => {
    if (isWl(c)) counts.whitelisted++;
    else { counts.all++; if (counts[c.domain] != null) counts[c.domain]++; }
    if (currentView !== "overview" && matchesView(c, currentView)) { acts.all++; acts[inferActionType(c)]++; }
  });
  Object.keys(counts).forEach(k => { const el = document.getElementById("cnt-" + k); if (el) el.textContent = counts[k] ? String(counts[k]) : ""; });
  Object.keys(acts).forEach(k => { const el = document.getElementById("acnt-" + k); if (el) el.textContent = String(acts[k]); });
}

function updateListHeader() {
  if (currentView === "overview") return;
  const scope = CANDS.filter(c => matchesView(c, currentView));
  const sel = selectionSummary(c => matchesView(c, currentView));
  const sub = document.getElementById("list-sub");
  if (sub) {
    sub.innerHTML = currentView === "whitelisted"
      ? `共 <b>${scope.length}</b> 项受保护。执行时会被 preflight 强制跳过，新加入的会写进导出指令。`
      : `共 <b>${scope.length}</b> 条建议，已选 <b>${sel.count}</b> 条${sel.bytes > 0 ? `，可释放 <b>${formatBytes(sel.bytes)}</b>` : ""}。点开一行可以看依据、写备注或加入白名单。`;
  }
  ["A", "B", "C"].forEach(t => {
    const el = document.getElementById("ghead-" + t);
    if (!el) return;
    const s = selectionSummary(c => matchesView(c, currentView) && tierOf(c) === t && (currentAction === "all" || inferActionType(c) === currentAction));
    el.textContent = `已选 ${s.count} 项${s.bytes ? "，" + formatBytes(s.bytes) : ""}`;
  });
}

function updateSelectionTotals(bump) {
  const sel = selectionSummary();
  const countEl = document.getElementById("selected-count");
  if (countEl) setNumericText(countEl, String(sel.count), sel.count);
  dockBytes.jump(sel.bytes);
  setNumericText(document.getElementById("selected-gib"), formatBytes(Math.max(0, sel.bytes)), sel.bytes);
  if (bump) popScale(document.getElementById("dock-stat"), popSprings, 0.94);
  updateCounts();
  updateListHeader();
  updateImpact();
  if (actionSeg && currentView !== "overview") actionSeg.refresh(true);
  try { renderRingOverview(currentView !== "overview"); } catch (e) {}
}

function openItem(id) {
  const c = candById[id];
  if (!c) return;
  const view = isWl(c) ? "whitelisted" : (DOMAIN_META[c.domain] ? c.domain : "all");
  currentAction = "all";
  navigate(view);
  if (c.gauge_id && gaugeById[c.gauge_id]) { activePoolId = c.gauge_id; updateImpact(); }
  requestAnimationFrame(() => {
    setExpanded(id, true, false);
    const card = document.getElementById("card-" + id);
    if (card) {
      card.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "center" });
      card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
    }
  });
}

/* ================= Toast ================= */
let toastTimer = 0;
function showToast(text) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1900);
}

/* ================= Custom frosted-glass popup selects & quick-select menu ================= */
let menuOpen = false;
let resetArmed = 0;
const popupMenus = [];
let poolPopup = null;
let sortPopup = null;

function closeAllPopups(except) {
  popupMenus.forEach(pm => { if (pm !== except && pm.isOpen()) pm.toggle(false); });
}

function setupPopupSelect(selectEl) {
  if (!selectEl || !selectEl.parentElement) return null;
  const wrap = selectEl.parentElement;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "popup-btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  const labelSpan = document.createElement("span");
  labelSpan.className = "popup-label";
  btn.appendChild(labelSpan);
  btn.insertAdjacentHTML("beforeend", '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10l5 5 5-5"/></svg>');
  wrap.appendChild(btn);

  const menu = document.createElement("div");
  menu.className = "menu popup-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  document.body.appendChild(menu);

  let open = false;
  const sp = new Spring(0, SPRING.sheet, v => {
    const p = Math.max(0, v);
    menu.style.opacity = clamp(p * 1.65, 0, 1).toFixed(3);
    menu.style.transform = "translateY(" + ((1 - p) * -8).toFixed(2) + "px) scale(" + (0.88 + 0.12 * p).toFixed(4) + ")";
  });
  sp.onRest = v => { if (v <= 0.001) menu.hidden = true; };

  function sync() {
    const opts = Array.from(selectEl.options);
    const activeOpt = opts.find(o => o.value === selectEl.value) || opts[0];
    labelSpan.textContent = activeOpt ? activeOpt.textContent : "";
    menu.innerHTML = opts.map(o => {
      const sel = o.value === selectEl.value;
      return `<button type="button" role="option" class="menu-opt" data-value="${escapeHtml(o.value)}" aria-selected="${sel}">` +
        `<svg class="opt-check" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7"/></svg>` +
        `<span>${escapeHtml(o.textContent)}</span></button>`;
    }).join("");
  }

  function positionMenu() {
    const r = btn.getBoundingClientRect();
    menu.style.minWidth = Math.max(168, Math.round(r.width + 20)) + "px";
    const w = menu.offsetWidth;
    const left = clamp(r.right - w, 10, window.innerWidth - w - 10);
    menu.style.left = left + "px";
    menu.style.top = (r.bottom + 7) + "px";
    menu.style.bottom = "auto";
    menu.style.transformOrigin = clamp(r.left + r.width / 2 - left, 16, w - 16) + "px 0%";
  }

  function toggle(force) {
    const next = force == null ? !open : Boolean(force);
    if (next === open && (next ? !menu.hidden : menu.hidden)) return;
    open = next;
    btn.setAttribute("aria-expanded", String(open));
    if (open) {
      closeAllPopups(api);
      if (menuOpen) toggleQuickMenu(false);
      sync();
      menu.hidden = false;
      positionMenu();
      sp.to(1, SPRING.sheet);
      const cur = menu.querySelector('.menu-opt[aria-selected="true"]') || menu.querySelector(".menu-opt");
      if (cur) cur.focus({ preventScroll: true });
    } else {
      sp.to(0, SPRING.soft);
    }
  }

  btn.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    toggle();
  });
  menu.addEventListener("click", e => {
    e.stopPropagation();
    const optBtn = e.target.closest(".menu-opt");
    if (!optBtn) return;
    const val = optBtn.getAttribute("data-value");
    if (selectEl.value !== val) {
      selectEl.value = val;
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    }
    sync();
    popScale(btn, popSprings, 0.95);
    toggle(false);
  });

  sync();
  const api = { sync, toggle, isOpen: () => open, positionMenu };
  popupMenus.push(api);
  return api;
}

const menuSpring = new Spring(0, SPRING.sheet, v => {
  const menu = document.getElementById("quick-menu");
  if (!menu) return;
  const p = Math.max(0, v);
  menu.style.opacity = clamp(p * 1.6, 0, 1).toFixed(3);
  menu.style.transform = "translateY(" + ((1 - p) * 10).toFixed(2) + "px) scale(" + (0.86 + 0.14 * p).toFixed(4) + ")";
});
menuSpring.onRest = v => { if (v <= 0.001) document.getElementById("quick-menu").hidden = true; };

function toggleQuickMenu(force) {
  const open = force == null ? !menuOpen : force;
  const menu = document.getElementById("quick-menu");
  const btn = document.getElementById("quick-btn");
  if (!menu || !btn) return;
  menuOpen = open;
  btn.setAttribute("aria-expanded", String(open));
  if (open) {
    closeAllPopups(null);
    menu.hidden = false;
    const r = btn.getBoundingClientRect();
    const w = menu.offsetWidth;
    const left = clamp(r.left + r.width / 2 - w / 2, 10, window.innerWidth - w - 10);
    menu.style.left = left + "px";
    menu.style.bottom = (window.innerHeight - r.top + 10) + "px";
    menu.style.transformOrigin = (r.left + r.width / 2 - left) + "px 100%";
    menuSpring.to(1, SPRING.sheet);
    const first = menu.querySelector("button");
    if (first) first.focus({ preventScroll: true });
  } else {
    disarmReset();
    menuSpring.to(0, SPRING.soft);
  }
}
function disarmReset() {
  resetArmed = 0;
  const item = document.getElementById("reset-item");
  if (item) { item.classList.remove("is-armed"); item.innerHTML = "恢复初始勾选<small>同时清掉备注和白名单改动</small>"; }
}
function handleQuick(action) {
  if (action === "reset") {
    if (!resetArmed) {
      resetArmed = Date.now();
      const item = document.getElementById("reset-item");
      item.classList.add("is-armed");
      item.innerHTML = "再点一次确认恢复<small>备注和白名单改动会被清掉，无法撤销</small>";
      return;
    }
    resetSavedState();
  } else if (action === "tierA") selectByTier(["A"]);
  else if (action === "tierAB") selectByTier(["A", "B"]);
  else if (action === "opt") selectOptimizations();
  else if (action === "clear") clearSelections();
  toggleQuickMenu(false);
}

/* ================= Execution prompt sheet (grows from the export button) ================= */
function buildExecutionPrompt() {
  const selected = [];
  const newlyWhitelisted = [];
  CANDS.forEach(c => {
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
  return lines.join("\n");
}

const sheetGeo = { dx: 0, dy: 0 };
const sheetSpring = new Spring(0, SPRING.sheet, v => {
  const back = document.getElementById("prompt-modal");
  const sheet = document.getElementById("prompt-sheet");
  if (!back || !sheet) return;
  const p = Math.max(0, v);
  back.style.opacity = clamp(p, 0, 1).toFixed(3);
  sheet.style.opacity = clamp(p * 1.8, 0, 1).toFixed(3);
  sheet.style.transform = `translate(${(sheetGeo.dx * (1 - p)).toFixed(2)}px, ${(sheetGeo.dy * (1 - p)).toFixed(2)}px) scale(${(0.35 + 0.65 * p).toFixed(4)})`;
});
sheetSpring.onRest = v => { if (v <= 0.001) document.getElementById("prompt-modal").hidden = true; };

function setSheetStatus(text, tone) {
  const el = document.getElementById("sheet-status");
  if (!el) return;
  el.textContent = text;
  el.className = tone || "";
}

function writeClipboard(text, textarea) {
  const fallback = () => {
    try {
      if (textarea) { textarea.focus(); textarea.select(); }
      return document.execCommand && document.execCommand("copy");
    } catch (e) { return false; }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(() => true, () => fallback());
  }
  return Promise.resolve(fallback());
}

function copyExecutionPrompt() {
  const text = buildExecutionPrompt();
  const back = document.getElementById("prompt-modal");
  const box = document.getElementById("prompt-textarea");
  const btn = document.getElementById("export-btn");
  box.value = text;
  back.hidden = false;
  if (btn) {
    const r = btn.getBoundingClientRect();
    sheetGeo.dx = r.left + r.width / 2 - window.innerWidth / 2;
    sheetGeo.dy = r.top + r.height / 2 - window.innerHeight / 2;
  }
  sheetSpring.to(1, SPRING.sheet);
  setSheetStatus("粘贴回对话框，Agent 会先跑 preflight 复查再动手。");
  writeClipboard(text, box).then(ok => {
    setSheetStatus(ok ? "已复制到剪贴板。粘贴回对话框，Agent 会先跑 preflight 复查再动手。" : "浏览器没允许自动复制，点「复制指令」再试，或者手动全选复制。", ok ? "ok" : "warn");
  });
  toggleQuickMenu(false);
}

function copyModalText() {
  const box = document.getElementById("prompt-textarea");
  const btn = document.getElementById("copy-confirm-btn");
  writeClipboard(box.value, box).then(ok => {
    setSheetStatus(ok ? "已复制到剪贴板。" : "还是没能复制，请在文本框里全选后手动复制。", ok ? "ok" : "warn");
    if (btn && ok) {
      btn.textContent = "已复制";
      popScale(btn, popSprings, 0.9);
      setTimeout(() => { btn.textContent = "复制指令"; }, 1400);
    }
  });
}

function closeModal() {
  sheetSpring.to(0, SPRING.soft);
  const btn = document.getElementById("export-btn");
  if (btn) btn.focus({ preventScroll: true });
}

/* ================= Wiring & boot ================= */
function wireEvents() {
  document.addEventListener("animationend", e => {
    if (e.animationName === "rise" && e.target && e.target.classList) e.target.classList.remove("enter");
  });
  document.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => navigate(b.getAttribute("data-view"))));

  const list = document.getElementById("candidate-list");
  list.addEventListener("click", e => {
    const actEl = e.target.closest("[data-act]");
    const row = e.target.closest(".row");
    if (!actEl || !row) return;
    const id = row.getAttribute("data-id");
    const act = actEl.getAttribute("data-act");
    if (act === "toggle") toggleItem(id);
    else if (act === "expand") setExpanded(id, !expandedRows.has(id), false);
    else if (act === "wl") toggleWhitelist(id);
  });
  list.addEventListener("input", e => {
    const input = e.target.closest('input[data-act="note"]');
    const row = e.target.closest(".row");
    if (input && row) updateNote(row.getAttribute("data-id"), input.value);
  });
  list.addEventListener("mouseover", e => {
    const row = e.target.closest(".row");
    const id = row ? row.getAttribute("data-id") : null;
    if (id && id !== focusedItemId) {
      focusedItemId = id;
      const c = candById[id];
      if (c && c.gauge_id && gaugeById[c.gauge_id]) activePoolId = c.gauge_id;
      updateImpact();
    }
  });
  list.addEventListener("mouseleave", () => { if (focusedItemId) { focusedItemId = null; updateImpact(); } });

  const poolSel = document.getElementById("impact-pool");
  poolSel.innerHTML = GAUGES.map(g => `<option value="${escapeHtml(g.id)}">${escapeHtml(poolTitle(g))}</option>`).join("");
  poolSel.addEventListener("change", () => { activePoolId = poolSel.value; lastToggledId = null; if (poolPopup) poolPopup.sync(); updateImpact(); });
  const sortSel = document.getElementById("sort-select");
  sortSel.addEventListener("change", e => { currentSort = e.target.value; if (sortPopup) sortPopup.sync(); renderCandidates(); animateListTransition(); });
  poolPopup = setupPopupSelect(poolSel);
  sortPopup = setupPopupSelect(sortSel);
  document.getElementById("select-visible-btn").addEventListener("click", selectVisibleCategory);

  document.getElementById("export-btn").addEventListener("click", copyExecutionPrompt);
  document.getElementById("copy-confirm-btn").addEventListener("click", copyModalText);
  document.getElementById("sheet-close").addEventListener("click", closeModal);
  document.getElementById("prompt-modal").addEventListener("click", e => { if (e.target === e.currentTarget) closeModal(); });
  document.getElementById("quick-btn").addEventListener("click", e => { e.stopPropagation(); toggleQuickMenu(); });
  document.getElementById("quick-menu").addEventListener("click", e => {
    e.stopPropagation();
    const b = e.target.closest("[data-quick]");
    if (b) handleQuick(b.getAttribute("data-quick"));
  });
  document.addEventListener("click", () => { if (menuOpen) toggleQuickMenu(false); closeAllPopups(null); });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!document.getElementById("prompt-modal").hidden) closeModal();
    else if (menuOpen) toggleQuickMenu(false);
    else closeAllPopups(null);
  });
  const schemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  if (schemeQuery && schemeQuery.addEventListener) schemeQuery.addEventListener("change", () => repaintAllRings());
  if (window.ResizeObserver) new ResizeObserver(() => paintRing()).observe(document.getElementById("ring-canvas"));
  window.addEventListener("resize", () => {
    placeNavPill(false);
    if (actionSeg) actionSeg.refresh(false);
    if (themeSeg) themeSeg.refresh(false);
    popupMenus.forEach(pm => { if (pm.isOpen()) pm.positionMenu(); });
  });
}

function bootstrapDashboard() {
  const theme = initTheme();
  refreshRingTheme();
  hydrateIcons(document);
  const timeEl = document.getElementById("brand-time");
  if (timeEl) timeEl.textContent = "快照 " + formatTime(timeEl.getAttribute("data-raw"));
  wireEvents();
  themeSeg = setupSegmented(document.getElementById("theme-seg"), theme, setTheme);
  actionSeg = setupSegmented(document.getElementById("action-seg"), currentAction, value => { currentAction = value; renderCandidates(); animateListTransition(); });
  try { renderDiff(); } catch (e) {}
  let start = "overview";
  try { const h = (location.hash || "").replace("#", ""); if (VIEWS.indexOf(h) >= 0) start = h; } catch (e) {}
  currentView = "";
  navigate(start, { keepScroll: true });
  updateSelectionTotals(false);
  requestAnimationFrame(() => placeNavPill(false));
}

bootstrapDashboard();
</script>
</body>
</html>
"""


HTML_TEMPLATE = TEMPLATE_HEAD + TEMPLATE_BODY + TEMPLATE_SCRIPT_CORE + TEMPLATE_SCRIPT_OVERVIEW + TEMPLATE_SCRIPT_WORKBENCH


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
    report_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    bounce_curve, bounce_ms = spring_easing(**SPRING_PRESETS["bounce"])
    soft_curve, soft_ms = spring_easing(**SPRING_PRESETS["soft"])

    out = HTML_TEMPLATE
    out = out.replace("__DARK_TOKENS__", DARK_TOKENS)
    out = out.replace("__EASE_BOUNCE_MS__", str(bounce_ms))
    out = out.replace("__EASE_SOFT_MS__", str(soft_ms))
    out = out.replace("__EASE_BOUNCE__", bounce_curve)
    out = out.replace("__EASE_SOFT__", soft_curve)
    out = out.replace("__SPRING_PRESETS__", json.dumps(SPRING_PRESETS))
    out = out.replace("__TITLE__", html.escape(title))
    out = out.replace("__HEADING__", html.escape(title))
    out = out.replace("__GENERATED_AT__", html.escape(generated_at))
    # Report data goes in last so placeholder-like text inside user data is never rewritten.
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
