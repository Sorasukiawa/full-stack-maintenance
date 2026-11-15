#!/usr/bin/env python3
"""Generate a single-file interactive HTML dashboard for full-stack maintenance.

Design system:
- macOS-style app window: translucent vibrancy sidebar with System Settings style icon tiles,
  large titles, inset grouped lists, and a floating glass action dock.
- Overview: one thick storage ring drawn like an Apple Health activity ring (round caps,
  gradient along the arc) with retained used / selected to free / hovered item segments,
  per-volume mini rings, segmented "space composition" bar, and a collapsible read-only
  environments & peripherals summary panel.
- Motion: every interactive change runs on real spring physics described with Apple's two
  designer parameters (damping ratio + response). Springs retarget from the current value and
  keep their velocity, so all motion is interruptible. CSS transitions use spring curves that
  are pre-computed here and emitted as ``linear()`` easing functions.
- Static frontend templates live in ``scripts/dashboard_assets/`` (``template.html``,
  ``styles.css``, ``app.js``) and are assembled inline into a self-contained zero-external-request
  HTML file.

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


ASSETS_DIR = Path(__file__).resolve().parent / "dashboard_assets"

# Apple-style spring presets (damping ratio, response in seconds).
# damping < 1 gives the light rebound; response is how quickly the value arrives.
SPRING_PRESETS: dict[str, dict[str, float]] = {
    "bounce": {"damping": 0.72, "response": 0.42},  # 勾选、按钮回弹、Dock 数字
    "glide": {"damping": 0.80, "response": 0.34},   # 分段控件滑块、侧栏选中块
    "ring": {"damping": 0.78, "response": 0.62},    # 圆环与水位条
    "sheet": {"damping": 0.84, "response": 0.44},   # 弹出面板、菜单
    "soft": {"damping": 0.92, "response": 0.36},    # 页面切换、展开收起（几乎无回弹）
}

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

_CANDIDATE_KEYS = (
    "id",
    "domain",
    "category",
    "kind",
    "action_type",
    "quarantine_allowed",
    "tier",
    "title",
    "path",
    "paths",
    "size_bytes",
    "reclaimable_bytes",
    "metric_badge",
    "gauge_id",
    "status",
    "whitelisted",
    "whitelist_note",
    "default_checked",
    "selected_uninstall_mode",
    "uninstall_modes",
    "evidence",
)

_GAUGE_KEYS = (
    "id",
    "label",
    "mount_path",
    "status",
    "note",
    "reason",
    "error",
    "total_bytes",
    "used_bytes",
    "free_bytes",
    "used_pct",
    "warn_used_pct",
    "warn_pct",
    "cleaned_bytes",
    "before_used_bytes",
    "breakdown",
)

_DEVICE_KEYS = (
    "name",
    "kind",
    "vendor",
    "model",
    "firmware_version",
    "latest_firmware",
    "update_available",
    "smart_status",
    "link_speed",
    "transport",
    "battery_pct",
    "status",
    "note",
)

MAX_ENV_PACKAGE_PREVIEW = 12
MAX_DIFF_GROWTH_PATHS = 20


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


def load_dashboard_assets(assets_dir: Path | None = None) -> tuple[str, str, str]:
    """Read split HTML, CSS, and JS assets from ``scripts/dashboard_assets/``."""
    base = assets_dir or ASSETS_DIR
    template_html = (base / "template.html").read_text(encoding="utf-8")
    styles_css = (base / "styles.css").read_text(encoding="utf-8")
    app_js = (base / "app.js").read_text(encoding="utf-8")
    return template_html, styles_css, app_js


def _compact_uninstall_modes(raw_modes: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(raw_modes, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for key, val in raw_modes.items():
        if not isinstance(val, dict):
            continue
        entry: dict[str, Any] = {}
        for k in ("label", "description", "size_bytes", "reclaimable_bytes", "paths"):
            if k in val and val[k] is not None:
                entry[k] = val[k]
        out[str(key)] = entry
    return out or None


def _compact_candidate(cand: Any) -> dict[str, Any]:
    if not isinstance(cand, dict):
        return {}
    out: dict[str, Any] = {}
    for k in _CANDIDATE_KEYS:
        if k not in cand or cand[k] is None:
            continue
        if k == "uninstall_modes":
            modes = _compact_uninstall_modes(cand[k])
            if modes:
                out[k] = modes
        else:
            out[k] = cand[k]
    return out


def _compact_gauge(gauge: Any) -> dict[str, Any]:
    if not isinstance(gauge, dict):
        return {}
    return {k: gauge[k] for k in _GAUGE_KEYS if k in gauge and gauge[k] is not None}


def _compact_app_inventory(raw_inv: Any) -> dict[str, Any] | None:
    """Keep summary counters only; strip unconsumed full `apps` array to avoid HTML bloat."""
    if not isinstance(raw_inv, dict):
        return None
    summary = {
        k: raw_inv[k]
        for k in ("total_apps", "frequent_count", "infrequent_count", "total_bytes")
        if k in raw_inv and raw_inv[k] is not None
    }
    return summary or None


def _compact_environments(raw_envs: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw_envs, list) or not raw_envs:
        return None
    out: list[dict[str, Any]] = []
    for grp in raw_envs:
        if not isinstance(grp, dict):
            continue
        pkgs = grp.get("packages") if isinstance(grp.get("packages"), list) else []
        compact_pkgs: list[Any] = []
        for p in pkgs[:MAX_ENV_PACKAGE_PREVIEW]:
            if isinstance(p, dict):
                compact_pkgs.append(
                    {k: p[k] for k in ("name", "current", "latest") if k in p and p[k] is not None}
                )
            else:
                compact_pkgs.append(str(p))
        total_pkgs = int(grp.get("count") or len(pkgs))
        entry: dict[str, Any] = {
            "id": grp.get("id") or grp.get("manager") or grp.get("ecosystem") or "env",
            "manager": grp.get("manager") or grp.get("ecosystem") or grp.get("id") or "开发环境",
            "total_packages": total_pkgs,
            "packages": compact_pkgs,
        }
        if grp.get("command"):
            entry["command"] = str(grp["command"])
        out.append(entry)
    return out or None


def _compact_peripherals(raw_periph: Any) -> dict[str, list[dict[str, Any]]] | None:
    if not isinstance(raw_periph, dict):
        return None
    out: dict[str, list[dict[str, Any]]] = {}
    has_any = False
    for group_key in ("storage_devices", "docks_and_hubs", "input_devices"):
        items = raw_periph.get(group_key)
        if not isinstance(items, list) or not items:
            out[group_key] = []
            continue
        cleaned = [
            {k: dev[k] for k in _DEVICE_KEYS if k in dev and dev[k] is not None}
            for dev in items
            if isinstance(dev, dict)
        ]
        out[group_key] = cleaned
        if cleaned:
            has_any = True
    return out if has_any else None


def _compact_diff(raw_diff: Any) -> dict[str, Any] | None:
    if not isinstance(raw_diff, dict):
        return None
    out: dict[str, Any] = {}
    for k in ("before_generated_at", "after_generated_at", "gauge_deltas", "new_candidate_ids", "resolved_candidate_ids"):
        if k in raw_diff and raw_diff[k] is not None:
            out[k] = raw_diff[k]
    growth = raw_diff.get("top_growth_paths")
    if isinstance(growth, list):
        out["top_growth_paths"] = growth[:MAX_DIFF_GROWTH_PATHS]
    return out or None


def build_dashboard_payload(
    snapshot: dict[str, Any],
    diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a compact, sanitized JSON payload for the embedded dashboard script."""
    generated_at = str(snapshot.get("generated_at") or "")
    candidates_raw = snapshot.get("candidates") if isinstance(snapshot.get("candidates"), list) else []
    gauges_raw = snapshot.get("storage_gauges") if isinstance(snapshot.get("storage_gauges"), list) else []
    raw_id_seed = f"{generated_at}:{len(candidates_raw)}"
    report_id = snapshot.get("report_id") or hashlib.sha256(raw_id_seed.encode("utf-8")).hexdigest()[:12]

    payload: dict[str, Any] = {
        "report_id": report_id,
        "generated_at": generated_at,
        "storage_gauges": [_compact_gauge(g) for g in gauges_raw if isinstance(g, dict)],
        "candidates": [_compact_candidate(c) for c in candidates_raw if isinstance(c, dict)],
    }
    app_inv = _compact_app_inventory(snapshot.get("app_inventory"))
    if app_inv is not None:
        payload["app_inventory"] = app_inv
    envs = _compact_environments(snapshot.get("environments"))
    if envs is not None:
        payload["environments"] = envs
    periph = _compact_peripherals(snapshot.get("peripherals"))
    if periph is not None:
        payload["peripherals"] = periph
    effective_diff = diff if diff is not None else snapshot.get("diff")
    compact_diff = _compact_diff(effective_diff)
    if compact_diff is not None:
        payload["diff"] = compact_diff
    return payload


def safe_json_for_script(data: Any) -> str:
    """Serialize JSON safely for inline ``<script>`` embedding (prevent ``</script>`` breakout)."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("</", "<\\/")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_dashboard_html(
    snapshot: dict[str, Any],
    diff: dict[str, Any] | None = None,
    title: str = "全栈健康巡检与空间水位交互看板",
) -> str:
    template_html, styles_css, app_js = load_dashboard_assets()
    payload = build_dashboard_payload(snapshot, diff=diff)
    generated_at = payload["generated_at"]
    report_json = safe_json_for_script(payload)

    bounce_curve, bounce_ms = spring_easing(**SPRING_PRESETS["bounce"])
    soft_curve, soft_ms = spring_easing(**SPRING_PRESETS["soft"])

    rendered_styles = (
        styles_css.replace("__DARK_TOKENS__", DARK_TOKENS)
        .replace("__EASE_BOUNCE_MS__", str(bounce_ms))
        .replace("__EASE_SOFT_MS__", str(soft_ms))
        .replace("__EASE_BOUNCE__", bounce_curve)
        .replace("__EASE_SOFT__", soft_curve)
    )
    rendered_script = app_js.replace("__SPRING_PRESETS__", json.dumps(SPRING_PRESETS, separators=(",", ":")))

    out = template_html
    out = out.replace("__INLINE_STYLES__", rendered_styles)
    out = out.replace("__INLINE_SCRIPT__", rendered_script)
    out = out.replace("__TITLE__", html.escape(title))
    out = out.replace("__HEADING__", html.escape(title))
    out = out.replace("__GENERATED_AT__", html.escape(generated_at))
    # Report data goes in last so placeholder-like text inside user data is never rewritten.
    out = out.replace("__REPORT_JSON__", report_json)
    return out


# Alias kept for external callers expecting `render_html`.
render_html = render_dashboard_html


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
