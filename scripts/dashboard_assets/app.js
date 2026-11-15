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

/* Apple SwiftUI .contentTransition(.numericText()) per-digit transition. */
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
function applyNumericSlotFrame(slot, dir, blurPeak, wEase) {
  const p = clamp(slot.v, 0, 1);
  if (slot.wA != null && slot.wB != null) {
    const we = wEase != null ? wEase : p;
    const w = slot.wA + (slot.wB - slot.wA) * we;
    slot.curW = w;
    slot.slotEl.style.width = w.toFixed(2) + "px";
  } else {
    const wCh = slot.wCh || 0.6;
    if (slot.isExit) {
      slot.slotEl.style.width = ((1 - p) * wCh).toFixed(3) + "em";
    } else if (slot.isEnter) {
      slot.slotEl.style.width = (p * wCh).toFixed(3) + "em";
    }
  }
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
  const numA = pA.intPart + pA.decPart;
  const numB = pB.intPart + pB.decPart;
  const maxNum = Math.max(numA.length, numB.length);
  const padRight = el.id !== "ring-center-num";
  const sA = padRight ? numA.padEnd(maxNum, " ") : numA.padStart(maxNum, " ");
  const sB = padRight ? numB.padEnd(maxNum, " ") : numB.padStart(maxNum, " ");
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
      reusedW: reused && !reused.done && reused.curW != null ? reused.curW : null,
      wA: null,
      wB: null,
      curW: null,
      delay: keepFlight ? 0 : Math.min(0.14, stagger * NUMERIC_STAGGER),
      dur: keepFlight ? Math.max(0.12, NUMERIC_SLOT_DUR * (1 - v0 * 0.55)) : NUMERIC_SLOT_DUR,
      u: 0,
      v0,
      v: v0,
      done: false
    };
    stagger++;
    slots.push(s);
  };
  for (let i = 0; i < sB.length; i++) {
    const ca = sA[i] === " " ? "" : sA[i];
    const cb = sB[i] === " " ? "" : sB[i];
    const key = "d" + (i - maxNum);
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
    const ch = document.createElement("span");
    ch.className = "nt-ch";
    ch.textContent = pB.suffix;
    wrap.appendChild(ch);
  }
  if (!slots.length) {
    springLoop.active.delete(info);
    info.slots = [];
    el.textContent = next;
    return;
  }
  el.replaceChildren(wrap);
  let totalDur = NUMERIC_SLOT_DUR;
  for (let i = 0; i < slots.length; i++) {
    const s = slots[i];
    const rawWA = s.oldEl ? s.oldEl.getBoundingClientRect().width : 0;
    const rawWB = s.newEl ? s.newEl.getBoundingClientRect().width : 0;
    s.wA = s.reusedW != null ? s.reusedW : rawWA;
    s.wB = rawWB;
    s.curW = s.wA;
    if (s.delay + s.dur > totalDur) totalDur = s.delay + s.dur;
    applyNumericSlotFrame(s, dir, info.blurPeak, 0);
  }
  info.totalDur = totalDur;
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
      totalDur: NUMERIC_SLOT_DUR,
      blurPeak: el.id === "ring-center-num" ? 9.5 : (el.id === "selected-gib" || (el.id && el.id.startsWith("rkpi-")) ? 3.8 : 2.2),
      slots: [],
      step(dt) {
        this.elapsed += dt;
        const wEase = numericEase(clamp(this.elapsed / NUMERIC_SLOT_DUR, 0, 1));
        let activeCount = 0;
        for (let i = 0; i < this.slots.length; i++) {
          const s = this.slots[i];
          if (s.done) {
            if (s.wA != null && s.wB != null && Math.abs(s.wA - s.wB) > 0.05) {
              const w = s.wA + (s.wB - s.wA) * wEase;
              s.curW = w;
              s.slotEl.style.width = w.toFixed(2) + "px";
            }
            continue;
          }
          if (this.elapsed <= s.delay) {
            if (s.wA != null && s.wB != null && Math.abs(s.wA - s.wB) > 0.05) {
              const w = s.wA + (s.wB - s.wA) * wEase;
              s.curW = w;
              s.slotEl.style.width = w.toFixed(2) + "px";
            }
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
          applyNumericSlotFrame(s, this.dir, this.blurPeak, wEase);
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
function setPlainNumericText(el, text) {
  if (!el) return;
  const info = numericState.get(el);
  if (info) {
    springLoop.active.delete(info);
    info.slots = [];
    info.text = text;
    info.shownText = text;
    info.val = 0;
  }
  el.textContent = text;
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
        return {
          checked: parsed.checked || {},
          notes: parsed.notes || {},
          whitelisted: parsed.whitelisted || {},
          uninstallModes: parsed.uninstallModes || {}
        };
      }
    }
  } catch (e) {}
  const checked = {};
  const whitelisted = {};
  CANDS.forEach(c => {
    if (c.whitelisted) whitelisted[c.id] = true;
    else if (c.default_checked != null ? c.default_checked : c.tier === "A") checked[c.id] = true;
  });
  return { checked, notes: {}, whitelisted, uninstallModes: {} };
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

/* ================= Candidate & storage model ================= */
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
const CATEGORY_LABELS = {
  app_orphan: "卸载残留清理",
  app_uninstall: "不常用应用卸载",
  system_cache: "全局工具纯缓存",
  launch_item: "开机启动项检查",
  app_update: "应用版本更新",
  env_update: "开发环境与依赖",
  peripheral_firmware: "外设与存储固件",
  build_artifact: "可重建构建产物",
  git_pack_bloat: "Git 对象膨胀",
  governance_doc: "治理与交接文档",
  backup_exclusion_drift: "备份排除漏排",
  /* Legacy & cross-domain compatible category keys */
  git_bloat: "Git 仓库膨胀",
  governance_drift: "工程规则缺失",
  firmware: "外设与存储固件",
  stale_worktree: "闲置工作树整理",
  merged_branch: "已合并分支清理",
  github_repo: "GitHub 仓库治理",
  github_actions: "GitHub Actions 缓存",
  ci_failure: "CI 工作流修复",
  docker_cache: "容器与镜像缓存",
  vm_snapshot: "虚拟机与快照整理",
  nas_snapshot: "NAS 快照与冗余",
  temp_env: "临时模拟器与虚拟机"
};
const CATEGORY_WORD_ACRONYMS = {
  git: "Git",
  nas: "NAS",
  pve: "PVE",
  vm: "VM",
  ci: "CI",
  sdk: "SDK",
  ios: "iOS",
  macos: "macOS",
  app: "App",
  db: "DB",
  ssh: "SSH",
  usb: "USB",
  nvme: "NVMe",
  ssd: "SSD"
};
const NON_RECLAIMABLE_CATEGORIES = new Set([
  "git_pack_bloat",
  "git_bloat",
  "backup_exclusion_drift",
  "governance_doc",
  "governance_drift",
  "launch_item",
  "app_update",
  "env_update",
  "peripheral_firmware",
  "firmware"
]);
const UNINSTALL_MODE_DEFAULTS = {
  keep_data: {
    cli: "keep-data",
    shortLabel: "保留用户数据",
    description: "仅移走 .app 本体与缓存，保留配置与存档数据"
  },
  purge_all: {
    cli: "purge-all",
    shortLabel: "一键全部删除含残留",
    description: "连带隔离检测到的关联数据、配置与缓存目录"
  }
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

function normalizeUninstallMode(raw) {
  const s = String(raw || "").trim().toLowerCase().replace(/-/g, "_");
  if (s === "purge_all") return "purge_all";
  if (s === "keep_data") return "keep_data";
  return "";
}
function getUninstallModeEntry(c, modeKey) {
  if (!c || !c.uninstall_modes) return null;
  const norm = normalizeUninstallMode(modeKey);
  if (!norm) return null;
  const hyph = norm.replace(/_/g, "-");
  return c.uninstall_modes[norm] || c.uninstall_modes[hyph] || null;
}
function hasDualUninstallModes(c) {
  return Boolean(getUninstallModeEntry(c, "keep_data") && getUninstallModeEntry(c, "purge_all"));
}
function uninstallModeOf(c) {
  if (!c || !c.uninstall_modes) return null;
  const saved = normalizeUninstallMode(state.uninstallModes && state.uninstallModes[c.id]);
  if (saved && getUninstallModeEntry(c, saved)) return saved;
  const preset = normalizeUninstallMode(c.selected_uninstall_mode);
  if (preset && getUninstallModeEntry(c, preset)) return preset;
  return "keep_data";
}
function uninstallModeDescription(c, modeKey) {
  const norm = normalizeUninstallMode(modeKey) || "keep_data";
  const entry = getUninstallModeEntry(c, norm);
  const custom = entry && typeof entry.description === "string" ? entry.description.trim() : "";
  if (custom) return custom;
  return (UNINSTALL_MODE_DEFAULTS[norm] && UNINSTALL_MODE_DEFAULTS[norm].description) || "";
}

function hasExplicitNumber(val) {
  if (typeof val === "number") return Number.isFinite(val);
  if (typeof val === "string" && val.trim() !== "") return Number.isFinite(Number(val));
  return false;
}

function sizeOf(c) {
  if (!c) return 0;
  if (c.uninstall_modes) {
    const mObj = getUninstallModeEntry(c, uninstallModeOf(c));
    if (mObj && mObj.size_bytes != null) return Math.max(0, Number(mObj.size_bytes) || 0);
  }
  return Math.max(0, Number(c.size_bytes) || 0);
}

/* Unified reclaimable disk space calculation:
 * 1. Explicit numeric `reclaimable_bytes` takes precedence (respecting dual-mode app selection).
 * 2. Legacy fallback counts `size_bytes` ONLY when `(action_type || "cleanup") === "cleanup"`
 *    and `category` is not a non-deletion governance/optimize/update category. */
function getReclaimableBytes(item) {
  if (!item) return 0;
  if (item.uninstall_modes) {
    const mObj = getUninstallModeEntry(item, uninstallModeOf(item));
    if (mObj) {
      if (hasExplicitNumber(mObj.reclaimable_bytes)) return Math.max(0, Number(mObj.reclaimable_bytes) || 0);
      if (hasExplicitNumber(item.reclaimable_bytes) && Number(item.reclaimable_bytes) === 0) return 0;
      if (hasExplicitNumber(mObj.size_bytes)) return Math.max(0, Number(mObj.size_bytes) || 0);
    }
  }
  if (hasExplicitNumber(item.reclaimable_bytes)) {
    return Math.max(0, Number(item.reclaimable_bytes) || 0);
  }
  const actionType = item.action_type || "cleanup";
  if (actionType !== "cleanup") return 0;
  if (item.category && NON_RECLAIMABLE_CATEGORIES.has(item.category)) return 0;
  return Math.max(0, Number(item.size_bytes) || 0);
}

function getReferenceBytes(item) {
  return sizeOf(item);
}

function activePathsOf(c) {
  if (c && c.uninstall_modes) {
    const mObj = getUninstallModeEntry(c, uninstallModeOf(c));
    if (mObj && Array.isArray(mObj.paths) && mObj.paths.length) return mObj.paths;
  }
  if (c && Array.isArray(c.paths) && c.paths.length) return c.paths;
  return c && c.path ? [c.path] : [];
}

function formatCategoryFallback(rawCategory, actionLabel) {
  const raw = String(rawCategory || "").trim();
  if (!raw) return actionLabel || "";
  if (CATEGORY_LABELS[raw]) return CATEGORY_LABELS[raw];
  if (/[\u4e00-\u9fff]/.test(raw)) return raw;
  const tokens = raw.split(/[_\-\s./]+/).filter(Boolean);
  if (!tokens.length) return actionLabel || "";
  const formatted = tokens.map(tok => {
    const low = tok.toLowerCase();
    if (CATEGORY_WORD_ACRONYMS[low]) return CATEGORY_WORD_ACRONYMS[low];
    return low.charAt(0).toUpperCase() + low.slice(1);
  }).join(" ");
  return actionLabel ? `${actionLabel} · ${formatted}` : formatted;
}

function categoryLabel(c) {
  if (!c) return "";
  const actLabel = ACTION_META[inferActionType(c)].label;
  if (!c.category) return actLabel;
  if (CATEGORY_LABELS[c.category]) return CATEGORY_LABELS[c.category];
  return formatCategoryFallback(c.category, actLabel);
}

function isWl(c) { return Boolean(state.whitelisted[c.id]); }
function isDone(c) { return c.status === "done"; }
function isChecked(c) { return !isWl(c) && !isDone(c) && Boolean(state.checked[c.id]); }
function tierOf(c) { return TIER_META[c.tier] ? c.tier : "B"; }

function inferActionType(c) {
  if (c.action_type && ACTION_META[c.action_type]) return c.action_type;
  if (c.category === "app_update" || c.category === "env_update" || c.category === "firmware") return "update";
  if (c.category === "governance_doc" || c.category === "governance_drift") return "organize";
  if (c.category === "git_pack_bloat" || c.category === "git_bloat" || c.category === "backup_exclusion_drift" || c.category === "launch_item" || c.category === "peripheral_firmware") return "optimize";
  if (c.category === "app_uninstall" || c.category === "app_orphan" || c.category === "system_cache" || c.category === "build_artifact") return "cleanup";
  const text = `${c.id || ""} ${c.title || ""} ${c.category || ""}`.toLowerCase();
  const has = words => words.some(w => text.includes(w));
  if (has(["更新", "升级", "outdated", "upgrade", "rustup", "firmware"])) return "update";
  if (has(["优化", "压缩", "pack", "fstrim", "规则", "drift", "修复", "ci_failure", "smart", "速率", "电量"])) return "optimize";
  if (has(["整理", "归档", "合并", "推送", "纳管", "规范", "软链", "governance", "unpushed", "unlinked"])) return "organize";
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

function isGaugeOffline(g) {
  if (!g) return true;
  if (g.status && g.status !== "ok") return true;
  return !(Number(g.total_bytes) > 0);
}
function gaugeOfflineNote(g) {
  if (!g) return "";
  return String(g.note || g.reason || g.error || "").trim();
}

function matchesView(c, view) {
  if (view === "whitelisted") return isWl(c);
  if (isWl(c)) return false;
  return view === "all" || c.domain === view;
}

/* Per-volume numbers used by the ring, the mini rings and the impact bar. */
function computePoolRingMetrics(g) {
  const offline = isGaugeOffline(g);
  const offlineNote = offline ? gaugeOfflineNote(g) : "";
  if (offline) {
    return {
      id: g.id,
      gauge: g,
      offline: true,
      offlineNote,
      label: stripLeadingEmoji(g.label || g.id),
      shortTitle: poolTitle(g),
      mountPath: g.mount_path || "",
      total: 0,
      retainedBytes: 0,
      cleanedBytes: 0,
      beforeUsedBytes: 0,
      freeBeforeBytes: 0,
      freeAfterBytes: 0,
      beforePct: 0,
      afterPct: 0,
      warnPct: warnPctOf(g),
      goalBytes: 0,
      hasDone: false
    };
  }
  const total = Number(g.total_bytes);
  const currentUsed = Math.max(0, Number(g.used_bytes) || 0);
  const currentFree = g.free_bytes != null ? Math.max(0, Number(g.free_bytes)) : Math.max(0, total - currentUsed);
  let doneBytes = 0;
  let checkedBytes = 0;
  let goalBytes = 0;
  CANDS.forEach(c => {
    if (c.gauge_id !== g.id || isWl(c)) return;
    const reclaim = getReclaimableBytes(c);
    if (!reclaim) return;
    goalBytes += reclaim;
    if (isDone(c)) doneBytes += reclaim;
    else if (state.checked[c.id]) checkedBytes += reclaim;
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
    offline: false,
    offlineNote: "",
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
  const onlineList = list.filter(m => !m.offline);
  const offlineList = list.filter(m => m.offline);
  const allOffline = list.length > 0 && onlineList.length === 0;
  const offlineNotes = offlineList.map(m => m.offlineNote ? `${m.shortTitle}：${m.offlineNote}` : m.shortTitle);
  const agg = onlineList.reduce((acc, m) => {
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
    id: "_all",
    gauge: null,
    offline: allOffline,
    offlineNote: offlineNotes.join("；"),
    onlineCount: onlineList.length,
    offlineCount: offlineList.length,
    offlinePools: offlineList,
    label: "全部存储卷",
    shortTitle: "全部存储卷",
    mountPath: "",
    total: 0,
    retainedBytes: 0,
    cleanedBytes: 0,
    beforeUsedBytes: 0,
    freeBeforeBytes: 0,
    freeAfterBytes: 0,
    beforePct: 0,
    afterPct: 0,
    warnPct: 101,
    goalBytes: 0,
    hasDone: false
  });
  if (allOffline) {
    agg.mountPath = `全部 ${list.length} 个存储卷均未挂载 / 离线`;
  } else if (offlineList.length > 0) {
    agg.mountPath = `${onlineList.length} 个可用存储卷合计（另有 ${offlineList.length} 个未挂载 / 离线）`;
    agg.beforePct = pct(agg.beforeUsedBytes, agg.total);
    agg.afterPct = pct(agg.retainedBytes, agg.total);
  } else {
    agg.mountPath = list.length + " 个存储卷合计";
    agg.beforePct = pct(agg.beforeUsedBytes, agg.total);
    agg.afterPct = pct(agg.retainedBytes, agg.total);
  }
  return { list, agg };
}

function selectionSummary(filterFn) {
  let count = 0, bytes = 0;
  CANDS.forEach(c => {
    if (filterFn && !filterFn(c)) return;
    if (isChecked(c)) {
      count++;
      bytes += getReclaimableBytes(c);
    }
  });
  return { count, bytes };
}

/* ================= Storage ring =================
 * One thick ring in the style of the Apple Health activity ring: round caps, a gradient
 * that follows the arc, and each segment's rounded head resting on the previous one with
 * a soft shadow. */
const defaultOnlineGauge = GAUGES.find(g => !isGaugeOffline(g)) || GAUGES[0];
let activeRingPoolId = defaultOnlineGauge ? defaultOnlineGauge.id : "_all";
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

const ringFlags = { isFocus: false, hasFree: false, isOffline: false };
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
    wrap.classList.toggle("is-offline", ringFlags.isOffline);
    wrap.classList.toggle("is-focus", !ringFlags.isOffline && ringFlags.isFocus);
    wrap.classList.toggle("is-free", !ringFlags.isOffline && !ringFlags.isFocus && ringFlags.hasFree);
  }
}

/* Interruptible spring animation of the ring segments (fractions 0..1) and the centre number. */
function animateRingTo(retFrac, clnFrac, focFrac, displayBytes, isFocus, isOffline) {
  ringFlags.isOffline = Boolean(isOffline);
  ringFlags.isFocus = !ringFlags.isOffline && Boolean(isFocus);
  ringFlags.hasFree = !ringFlags.isOffline && displayBytes > 0;
  ringSprings.ret.to(ringFlags.isOffline ? 0 : clamp(retFrac, 0, 1), SPRING.ring);
  ringSprings.cln.to(ringFlags.isOffline ? 0 : clamp(clnFrac, 0, 1), SPRING.ring);
  ringSprings.foc.to(ringFlags.isOffline ? 0 : clamp(focFrac, 0, 1), isFocus ? SPRING.glide : SPRING.ring);
  ringSprings.bytes.jump(ringFlags.isOffline ? 0 : displayBytes);
  if (ringFlags.isOffline) {
    setPlainNumericText(document.getElementById("ring-center-num"), "离线");
    setPlainNumericText(document.getElementById("ring-center-unit"), "");
  } else {
    const pair = bytePair(Math.max(0, displayBytes));
    setNumericText(document.getElementById("ring-center-num"), pair.num, displayBytes);
    setNumericText(document.getElementById("ring-center-unit"), pair.unit, displayBytes);
  }
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
    <button type="button" class="vol glass enter${m.offline ? " is-offline" : ""}" role="tab" style="--i:${i}" id="mring-card-${escapeHtml(m.id)}" data-pool="${escapeHtml(m.id)}" aria-selected="false">
      <span class="vol-ring-wrap"><canvas class="vol-ring" id="mring-canvas-${escapeHtml(m.id)}" width="46" height="46" aria-hidden="true"></canvas><span class="vol-pct" id="mring-pct-${escapeHtml(m.id)}"></span></span>
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
    if (card) {
      card.setAttribute("aria-selected", String(m.id === activeId));
      card.classList.toggle("is-offline", Boolean(m.offline));
    }
    const sp = miniSprings[m.id];
    if (sp) {
      if (m.offline || !(m.total > 0)) {
        sp.ret.to(0);
        sp.cln.to(0);
      } else {
        sp.ret.to(m.retainedBytes / m.total);
        sp.cln.to(m.cleanedBytes / m.total);
      }
    }
    const pctEl = document.getElementById("mring-pct-" + m.id);
    if (pctEl) {
      if (m.offline) {
        pctEl.textContent = "离线";
        pctEl.className = "vol-pct offline";
      } else {
        pctEl.textContent = m.afterPct.toFixed(0) + "%";
        pctEl.className = "vol-pct " + (m.id === "_all" ? "" : levelFor(m.afterPct, m.warnPct));
      }
    }
    const metaEl = document.getElementById("mring-meta-" + m.id);
    if (metaEl) {
      if (m.offline) {
        metaEl.className = "vol-meta offline";
        const reason = m.offlineNote ? `未挂载 / 离线 · ${m.offlineNote}` : "未挂载 / 离线";
        metaEl.textContent = reason;
        metaEl.title = reason;
      } else if (m.cleanedBytes > 0) {
        metaEl.className = "vol-meta free";
        const offSuffix = m.id === "_all" && m.offlineCount > 0 ? ` · ${m.offlineCount} 个离线` : "";
        metaEl.textContent = (m.hasDone ? "已释放 " : "可释放 ") + formatShort(m.cleanedBytes) + offSuffix;
        metaEl.title = metaEl.textContent;
      } else {
        metaEl.className = "vol-meta";
        const offSuffix = m.id === "_all" && m.offlineCount > 0 ? `（${m.offlineCount} 个离线）` : "";
        metaEl.textContent = "可用 " + formatShort(m.freeAfterBytes) + (offSuffix || ("，共 " + formatShort(m.total)));
        metaEl.title = metaEl.textContent;
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
  if (!active || active.offline || !(active.total > 0)) return [];
  let raw = [];
  if (active.id === "_all") {
    raw = metricsList.filter(m => !m.offline).map((m, i) => ({
      key: "s" + i, name: m.shortTitle, bytes: m.beforeUsedBytes, color: COMP_COLORS[i % COMP_COLORS.length], free: m.cleanedBytes
    }));
  } else {
    const g = active.gauge;
    const parts = [];
    const freeByCat = {};
    CANDS.forEach(c => {
      if (c.gauge_id !== g.id || !isChecked(c)) return;
      const reclaim = getReclaimableBytes(c);
      if (!reclaim) return;
      const cat = categoryLabel(c) || "其他";
      freeByCat[cat] = (freeByCat[cat] || 0) + reclaim;
    });
    if (Array.isArray(g.breakdown) && g.breakdown.length) {
      g.breakdown.forEach(b => parts.push({ name: String(b.name || b.label || "未命名"), bytes: Math.max(0, Number(b.size_bytes) || 0) }));
    } else {
      const byCat = {};
      CANDS.forEach(c => {
        if (c.gauge_id !== g.id) return;
        const reclaim = getReclaimableBytes(c);
        if (!reclaim) return;
        const cat = categoryLabel(c) || "其他";
        byCat[cat] = (byCat[cat] || 0) + reclaim;
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
  if (subEl) {
    subEl.textContent = active.offline ? "未挂载 / 离线" : ("清理前已用 " + formatShort(active.beforeUsedBytes));
  }
  for (let i = 0; i < COMP_SLOTS; i++) {
    const p = parts[i];
    const seg = box.querySelector(`#comp-bar i[data-slot="${i}"]`);
    const lg = box.querySelector(`#comp-legend span[data-slot="${i}"]`);
    const rawW = p && active.total > 0 ? clamp(pct(p.bytes, active.total), 0, 100) : 0;
    const w = rawW > 0.05 ? Math.max(2.0, rawW) : 0;
    if (seg) {
      if (p && w > 0) {
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
  if (agg && agg.offline) {
    parts.push(`当前全部存储卷均处于<b>未挂载 / 离线</b>状态。`);
  } else if (agg && agg.total > 0) {
    const offHint = agg.offlineCount > 0 ? `（已排除 <b>${agg.offlineCount}</b> 个未挂载 / 离线存储卷）` : "";
    parts.push(`可用存储卷清理后合计可用 <b>${formatBytes(agg.freeAfterBytes)}</b>${offHint}。`);
  }
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
  if (hero) hero.hidden = false;
  if (heroEmpty) heroEmpty.hidden = true;
  const entries = list.length > 1 ? list.concat([agg]) : list;
  const active = activeRingPoolId === "_all" ? agg : (list.find(m => m.id === activeRingPoolId) || list[0]);
  activeRingPoolId = active.id;
  if (!onlyUpdateArcs) updateMiniRings(entries, active.id);

  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const setHtml = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  if (hero) hero.classList.toggle("is-offline", Boolean(active.offline));

  if (active.offline) {
    animateRingTo(0, 0, 0, 0, false, true);
    setText("ring-center-kicker", "存储卷状态");
    setText("ring-center-water", "未挂载 / 离线");
    if (onlyUpdateArcs) return;
    setText("ring-active-title", active.shortTitle);
    setHtml("ring-active-mount", active.id === "_all"
      ? `<code>${escapeHtml(active.mountPath)}</code><span>所有存储卷当前均不可用</span>`
      : `<code>${escapeHtml(active.mountPath || active.id)}</code><span>${escapeHtml(active.offlineNote || "未挂载 / 离线，不计入汇总水位")}</span>`);
    setPlainNumericText(document.getElementById("rkpi-retained"), "—");
    setPlainNumericText(document.getElementById("rkpi-cleaned"), "—");
    setPlainNumericText(document.getElementById("rkpi-free-after"), "—");
    setPlainNumericText(document.getElementById("rkpi-total"), "—");
    const badge = document.getElementById("ring-active-badge");
    if (badge) {
      badge.className = "status-pill offline";
      badge.textContent = "未挂载 / 离线";
    }
    const desc = document.getElementById("ring-active-desc");
    if (desc) {
      if (active.id === "_all") {
        desc.innerHTML = `全部存储卷当前均处于<b>未挂载 / 离线</b>状态${active.offlineNote ? `（${escapeHtml(active.offlineNote)}）` : ""}，容量与水位数据暂不可用。`;
      } else {
        desc.innerHTML = `该存储卷当前<b>未挂载 / 离线</b>${active.offlineNote ? `（${escapeHtml(active.offlineNote)}）` : ""}，容量与水位暂不可用，不计入全部存储卷汇总。`;
      }
    }
    renderComposition(active, list);
    renderOverviewLists(active);
    return;
  }

  const total = active.total;
  const retained = Math.min(total, active.retainedBytes);
  const cleaned = Math.min(total - retained, active.cleanedBytes);
  let focusBytes = 0;
  let focusFromRetained = false;
  let hovered = hoveredRingItemId ? candById[hoveredRingItemId] : null;
  if (hovered && (active.id === "_all" ? (!gaugeById[hovered.gauge_id] || !isGaugeOffline(gaugeById[hovered.gauge_id])) : hovered.gauge_id === active.id)) {
    const hoveredReclaim = getReclaimableBytes(hovered);
    const hoveredProtBytes = hoveredReclaim || (NON_RECLAIMABLE_CATEGORIES.has(hovered.category) ? 0 : getReferenceBytes(hovered));
    if ((isChecked(hovered) || isDone(hovered)) && hoveredReclaim > 0) {
      focusBytes = Math.min(cleaned, hoveredReclaim);
    } else if (isWl(hovered) && hoveredProtBytes > 0) {
      focusBytes = Math.min(retained, hoveredProtBytes);
      focusFromRetained = focusBytes > 0;
    } else {
      hovered = null;
    }
  } else {
    hovered = null;
  }
  const retArc = focusFromRetained ? Math.max(0, retained - focusBytes) : retained;
  const other = focusFromRetained ? cleaned : Math.max(0, cleaned - focusBytes);
  animateRingTo(retArc / total, other / total, focusBytes / total, focusBytes > 0 ? focusBytes : cleaned, focusBytes > 0, false);

  setText("ring-center-kicker", hovered ? displayTitle(hovered) : (active.hasDone ? "已释放" : "预计可释放"));
  setText("ring-center-water", cleaned > 0
    ? `水位 ${fmtPct(active.beforePct)} → ${fmtPct(active.afterPct)}`
    : `当前水位 ${fmtPct(active.afterPct)}`);
  if (onlyUpdateArcs) return;
  setText("ring-active-title", active.shortTitle);
  setHtml("ring-active-mount", active.id === "_all"
    ? `<code>${escapeHtml(active.mountPath)}</code><span>${active.offlineCount > 0 ? `仅统计在线卷 · ${active.offlineCount} 个存储卷未挂载 / 离线` : "按各卷警戒线监控"}</span>`
    : `<code>${escapeHtml(active.mountPath || active.id)}</code><span>警戒线 ${fmtPct(active.warnPct)}</span>`);
  setNumericText(document.getElementById("rkpi-retained"), formatBytes(retained), retained);
  setNumericText(document.getElementById("rkpi-cleaned"), cleaned > 0 ? formatBytes(cleaned) : "0 GiB", cleaned);
  setNumericText(document.getElementById("rkpi-free-after"), formatBytes(active.freeAfterBytes), active.freeAfterBytes);
  setNumericText(document.getElementById("rkpi-total"), formatBytes(total), total);

  const badge = document.getElementById("ring-active-badge");
  const desc = document.getElementById("ring-active-desc");
  if (badge) {
    if (active.id === "_all") {
      const onlineOnly = list.filter(m => !m.offline);
      const tight = onlineOnly.filter(m => m.afterPct >= m.warnPct).length;
      badge.className = "status-pill " + (tight ? "warn" : "ok");
      badge.textContent = tight
        ? `${tight} 个卷清理后仍偏满`
        : (active.offlineCount > 0 ? `在线卷均在警戒线以下 · ${active.offlineCount} 个离线` : "清理后都在警戒线以下");
    } else {
      const before = levelFor(active.beforePct, active.warnPct);
      const after = levelFor(active.afterPct, active.warnPct);
      badge.className = "status-pill " + after;
      badge.textContent = after === "crit" ? "空间紧张" : (before !== "ok" && after === "ok" ? "清理后恢复正常" : (after === "warn" ? "接近警戒线" : "状态良好"));
    }
  }
  if (desc) {
    const nSel = CANDS.filter(c => {
      if (!isChecked(c) || !(getReclaimableBytes(c) > 0)) return false;
      if (active.id === "_all") {
        const g = gaugeById[c.gauge_id];
        return !g || !isGaugeOffline(g);
      }
      return c.gauge_id === active.id;
    }).length;
    const offlineClause = active.id === "_all" && active.offlineCount > 0
      ? `（另有 <b>${active.offlineCount}</b> 个未挂载 / 离线存储卷未计入汇总${active.offlineNote ? `：${escapeHtml(active.offlineNote)}` : ""}）`
      : "";
    if (cleaned > 0) {
      desc.innerHTML = `${active.hasDone ? "已经" : `勾选的 <b>${nSel}</b> 项会`}在${active.id === "_all" ? "可用存储卷上" : "这块盘上"}释放 <b>${formatBytes(cleaned)}</b>，水位从 <b>${fmtPct(active.beforePct)}</b> 降到 <b>${fmtPct(active.afterPct)}</b>${offlineClause}。`;
    } else {
      desc.innerHTML = `${active.id === "_all" ? "可用存储卷" : "这块盘"}还没有选中要清理的项目，当前水位 <b>${fmtPct(active.afterPct)}</b>，警戒线 ${active.id === "_all" ? "按各卷设置" : fmtPct(active.warnPct)}${offlineClause}。`;
    }
  }
  renderComposition(active, list);
  if (onlyUpdateArcs) return;
  renderOverviewLists(active);
}

/* ================= Overview: collapsible environments & peripherals ================= */
let envPeriphOpen = false;
let envPeriphSpring = null;

function renderEnvAndPeripherals() {
  const sec = document.getElementById("env-periph-section");
  const summaryEl = document.getElementById("env-periph-summary");
  const contentEl = document.getElementById("env-periph-content");
  const toggleBtn = document.getElementById("env-periph-toggle");
  const bodyEl = document.getElementById("env-periph-body");
  if (!sec || !summaryEl || !contentEl || !toggleBtn || !bodyEl) return;

  const envs = Array.isArray(REPORT_DATA.environments) ? REPORT_DATA.environments.filter(Boolean) : [];
  const periph = REPORT_DATA.peripherals && typeof REPORT_DATA.peripherals === "object" ? REPORT_DATA.peripherals : null;
  const drives = periph && Array.isArray(periph.storage_devices) ? periph.storage_devices : [];
  const docks = periph && Array.isArray(periph.docks_and_hubs) ? periph.docks_and_hubs : [];
  const inputs = periph && Array.isArray(periph.input_devices) ? periph.input_devices : [];
  const allDevices = []
    .concat(drives.map(d => ({ ...d, _group: "存储外设" })))
    .concat(docks.map(d => ({ ...d, _group: "扩展坞与集线器" })))
    .concat(inputs.map(d => ({ ...d, _group: "输入设备" })));

  if (!envs.length && !allDevices.length) {
    sec.hidden = true;
    return;
  }
  sec.hidden = false;

  const summaryParts = [];
  if (envs.length) {
    const pkgTotal = envs.reduce((s, g) => s + (Number(g.total_packages || g.count) || (Array.isArray(g.packages) ? g.packages.length : 0)), 0);
    summaryParts.push(`${envs.length} 组开发环境待更新${pkgTotal ? `（共 ${pkgTotal} 个包）` : ""}`);
  }
  if (allDevices.length) {
    summaryParts.push(`${allDevices.length} 台已连接存储/外设硬件`);
  }
  summaryEl.textContent = summaryParts.join(" · ") + " —— 点击展开查看明细";

  let htmlParts = "";
  if (envs.length) {
    const envItems = envs.map(grp => {
      const name = String(grp.manager || grp.ecosystem || grp.id || "开发环境");
      const pkgs = Array.isArray(grp.packages) ? grp.packages : [];
      const totalPkgs = Number(grp.total_packages || grp.count) || pkgs.length;
      const preview = pkgs.slice(0, 6).map(p => {
        if (p && typeof p === "object") {
          const cur = p.current ? `${p.current} → ` : "";
          return `${p.name || "?"} (${cur}${p.latest || "?"})`;
        }
        return String(p);
      }).join("、");
      const more = totalPkgs > 6 ? ` 等共 ${totalPkgs} 项` : "";
      const cmdHtml = grp.command ? ` · 升级命令 <code>${escapeHtml(grp.command)}</code>` : "";
      return `<div class="ro-item">
        <div class="ro-item-head"><span class="ro-item-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span><span class="chip info">${totalPkgs} 项可更新</span></div>
        <div class="ro-item-sub">${escapeHtml(preview + more)}${cmdHtml}</div>
      </div>`;
    }).join("");
    htmlParts += `<div class="ro-group">
      <h3 class="ro-group-title"><span>开发环境与依赖</span><span>${envs.length} 组</span></h3>
      <div class="ro-list">${envItems}</div>
    </div>`;
  }

  if (allDevices.length) {
    const devItems = allDevices.map(dev => {
      const name = String(dev.name || dev.model || "未命名外设");
      const meta = [dev._group, dev.vendor, dev.transport, dev.link_speed].filter(Boolean);
      if (dev.firmware_version) {
        meta.push(dev.latest_firmware && dev.update_available
          ? `固件 ${dev.firmware_version} → ${dev.latest_firmware}`
          : `固件 ${dev.firmware_version}`);
      }
      let badgeCls = "good";
      let badgeTxt = "正常";
      const smart = String(dev.smart_status || "").toLowerCase();
      if (smart && smart !== "verified" && smart !== "ok" && smart !== "supported") {
        badgeCls = "crit";
        badgeTxt = `SMART: ${dev.smart_status}`;
      } else if (dev.update_available) {
        badgeCls = "warn";
        badgeTxt = "固件可更新";
      } else if (typeof dev.battery_pct === "number") {
        badgeCls = dev.battery_pct <= 20 ? "warn" : "good";
        badgeTxt = `电量 ${dev.battery_pct}%`;
      } else if (dev.smart_status) {
        badgeTxt = `SMART ${dev.smart_status}`;
      }
      return `<div class="ro-item">
        <div class="ro-item-head"><span class="ro-item-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span><span class="chip ${badgeCls}">${escapeHtml(badgeTxt)}</span></div>
        <div class="ro-item-sub">${escapeHtml(meta.join(" · "))}</div>
      </div>`;
    }).join("");
    htmlParts += `<div class="ro-group">
      <h3 class="ro-group-title"><span>外设与存储固件</span><span>${allDevices.length} 台</span></h3>
      <div class="ro-list">${devItems}</div>
    </div>`;
  }
  contentEl.innerHTML = htmlParts;

  if (!toggleBtn.dataset.wired) {
    toggleBtn.dataset.wired = "1";
    toggleBtn.addEventListener("click", () => {
      envPeriphOpen = !envPeriphOpen;
      toggleBtn.setAttribute("aria-expanded", String(envPeriphOpen));
      if (!envPeriphSpring) {
        envPeriphSpring = new Spring(0, SPRING.soft, v => {
          bodyEl.style.height = Math.max(0, v).toFixed(2) + "px";
        }, { tolerance: 0.5 });
      }
      if (envPeriphOpen) {
        bodyEl.hidden = false;
        bodyEl.style.height = "auto";
        const target = Math.max(1, bodyEl.scrollHeight);
        bodyEl.style.height = "0px";
        envPeriphSpring.value = 0;
        envPeriphSpring.onRest = () => { if (envPeriphOpen) bodyEl.style.height = "auto"; };
        envPeriphSpring.to(target, SPRING.soft);
      } else {
        const startH = bodyEl.offsetHeight;
        envPeriphSpring.value = startH;
        envPeriphSpring.onRest = () => { if (!envPeriphOpen) { bodyEl.hidden = true; bodyEl.style.height = ""; } };
        envPeriphSpring.to(0, SPRING.soft);
      }
    });
  }
}

/* ================= Overview: detail lists ================= */
const expandedOverviewGroups = new Set();

function formatReferenceBadge(c) {
  const refSz = getReferenceBytes(c);
  const badge = String(c.metric_badge || "").trim();
  if (refSz > 0) {
    const refText = `参考体量 ${formatBytes(refSz)}`;
    if (!badge) return refText;
    if (/([kmgt]i?b)/i.test(badge)) return badge;
    return `${badge} · ${refText}`;
  }
  return badge;
}

function overviewRow(c, opts) {
  const act = ACTION_META[inferActionType(c)];
  const g = gaugeById[c.gauge_id];
  const subParts = [];
  if (g) subParts.push(escapeHtml(poolTitle(g)));
  if (c.path) subParts.push(`<code>${escapeHtml(c.path)}</code>`);
  let right = "";
  if (opts.kind === "space") {
    const reclaim = getReclaimableBytes(c);
    right = `<span class="amount free">${formatBytes(reclaim)}</span><span class="minibar"><i style="width:${Math.max(4, pct(reclaim, opts.max)).toFixed(1)}%"></i></span>`;
  } else if (opts.kind === "protected") {
    const refSz = getReferenceBytes(c);
    right = `<span class="amount muted">${refSz > 0 ? "保留 " + formatBytes(refSz) : "已锁定"}</span>`;
  } else {
    const refBadge = formatReferenceBadge(c);
    right = `<span class="amount muted">${escapeHtml(refBadge || (isDone(c) ? "已完成" : "待执行"))}</span>`;
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
  const space = scope.filter(c => !isWl(c) && (isDone(c) || state.checked[c.id]) && getReclaimableBytes(c) > 0)
    .sort((a, b) => getReclaimableBytes(b) - getReclaimableBytes(a));
  const opt = scope.filter(c => !isWl(c) && (isDone(c) || state.checked[c.id]) && getReclaimableBytes(c) === 0);
  const prot = scope.filter(c => isWl(c));
  const spaceSum = space.reduce((s, c) => s + getReclaimableBytes(c), 0);
  const max = Math.max(1, ...space.map(getReclaimableBytes));
  const where = escapeHtml(active && active.id !== "_all" ? active.shortTitle : "所有存储卷");
  let out = "";
  if (space.length) {
    out += overviewGroup("space", "将释放空间", `${where}，${space.length} 项，共 ${formatBytes(spaceSum)}`, space, { kind: "space", max });
  } else {
    out += overviewEmptyGroup("将释放空间", `${where}，0 项`, "trash", "green", `${where}还没有选中会释放空间的项目`, "去全部建议里勾选后，环形图与水位会实时变化", "去全部建议 →");
  }
  if (opt.length) {
    out += overviewGroup("opt", "优化与整理", `${where}，${opt.length} 项，不计入直接释放空间`, opt, { kind: "opt" });
  } else {
    out += overviewEmptyGroup("优化与整理", `${where}，0 项`, "bolt", "orange", `${where}没有待执行的优化与整理项`, "结构整理、Git 压缩、备份规则核对与版本更新会归在这里", "");
  }
  if (prot.length) {
    out += overviewGroup("prot", "受保护", `${where}，${prot.length} 项，执行时一律跳过`, prot, { kind: "protected" });
  } else {
    out += overviewEmptyGroup("受保护", `${where}，0 项`, "shield", "orange", `${where}没有受白名单保护的条目`, "在任意建议详情里点「加入白名单」即可锁定保护", "");
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
      const zero = d === 0;
      return `<div class="cell hoverable">
          <span class="app-icon tint-${zero ? "graphite" : (grow ? "orange" : "green")}">${icon("gauge")}</span>
          <span class="cell-text"><span class="cell-title"><span class="cell-title-text">${escapeHtml(poolTitle(gaugeById[x.id] || { id: x.id, label: x.label || x.id }))}</span></span>
            <span class="cell-sub">已用空间${grow ? "增加" : (d < 0 ? "减少" : "没有变化")}</span></span>
          <span class="cell-right"><span class="amount ${zero ? "muted" : (grow ? "grow" : "free")}">${grow ? "+" : (d < 0 ? "−" : "")}${formatBytes(Math.abs(d))}</span>
            <span class="minibar ${grow ? "grow" : ""}"><i style="width:${zero ? 0 : Math.max(4, pct(Math.abs(d), maxG)).toFixed(1)}%"></i></span></span>
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

/* ================= Navigation ================= */
const VIEWS = ["overview", "all", "mac", "projects", "github", "nas", "whitelisted"];
let currentView = "overview";
let currentAction = "all";
let currentSort = "default";
let activePoolId = defaultOnlineGauge ? defaultOnlineGauge.id : "";
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
  const firstVisible = CANDS.find(c => matchesView(c, currentView) && c.gauge_id && gaugeById[c.gauge_id] && !isGaugeOffline(gaugeById[c.gauge_id]) && tierOf(c) === "A")
    || CANDS.find(c => matchesView(c, currentView) && c.gauge_id && gaugeById[c.gauge_id]);
  if (firstVisible && currentView !== "all") {
    activePoolId = firstVisible.gauge_id;
    return;
  }
  const byPool = {};
  CANDS.forEach(c => {
    if (!c.gauge_id || !gaugeById[c.gauge_id] || !matchesView(c, currentView)) return;
    byPool[c.gauge_id] = (byPool[c.gauge_id] || 0) + (tierOf(c) === "A" ? 1e15 : 0) + (isChecked(c) ? getReclaimableBytes(c) : 0) + 1;
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
  const byReclaim = (a, b) => (getReclaimableBytes(b) - getReclaimableBytes(a)) || (getReferenceBytes(b) - getReferenceBytes(a));
  if (currentSort === "size") items.sort(byReclaim);
  else if (currentSort === "pool") items.sort((a, b) => String(a.gauge_id || "~").localeCompare(String(b.gauge_id || "~")) || byReclaim(a, b));
  else if (currentSort === "opt") items.sort((a, b) => ACTION_META[inferActionType(a)].rank - ACTION_META[inferActionType(b)].rank || byReclaim(a, b));
  return items;
}

function visibleItems() {
  return CANDS.filter(c => matchesView(c, currentView) && (currentAction === "all" || inferActionType(c) === currentAction));
}

function impactText(c) {
  const g = gaugeById[c.gauge_id];
  const reclaim = getReclaimableBytes(c);
  const refSz = getReferenceBytes(c);
  if (!reclaim) {
    return refSz > 0
      ? `参考体量 ${formatBytes(refSz)}（治理/优化项，不计入直接释放空间）`
      : "不改变磁盘占用";
  }
  if (!g) return `预计释放 ${formatBytes(reclaim)}`;
  const m = computePoolRingMetrics(g);
  if (m.offline) {
    return `预计释放 ${formatBytes(reclaim)}（所属存储卷 ${poolTitle(g)} 当前未挂载 / 离线）`;
  }
  const share = pct(reclaim, m.total);
  if (isChecked(c)) return `占 ${poolTitle(g)} ${fmtPct(share)}，已算进清理后水位 ${fmtPct(m.afterPct)}`;
  return `占 ${poolTitle(g)} ${fmtPct(share)}，勾选后水位 ${fmtPct(m.afterPct)} → ${fmtPct(Math.max(0, m.afterPct - share))}`;
}

function rowHtml(c) {
  const act = ACTION_META[inferActionType(c)];
  const tier = TIER_META[tierOf(c)];
  const wl = isWl(c), done = isDone(c), checked = isChecked(c);
  const reclaim = getReclaimableBytes(c);
  const refBadge = formatReferenceBadge(c);
  const g = gaugeById[c.gauge_id];
  const id = escapeHtml(c.id);
  const title = escapeHtml(displayTitle(c));
  const note = state.notes[c.id] != null ? state.notes[c.id] : (c.whitelist_note || "");
  const sizeHtml = reclaim > 0
    ? `<span class="row-size">${formatBytes(reclaim)}</span>`
    : `<span class="row-size badge">${escapeHtml(refBadge)}</span>`;
  const statusChip = done ? '<span class="chip done">✓ 已执行</span>' : (wl ? '<span class="chip warn">受保护</span>' : "");
  const activePaths = activePathsOf(c);
  const pathsHtml = activePaths.length
    ? `<div class="path-line">${activePaths.map(p => `<code>${escapeHtml(p)}</code>`).join("")}</div>`
    : "";
  let uModeHtml = "";
  if (hasDualUninstallModes(c)) {
    const curMode = uninstallModeOf(c);
    const pMode = getUninstallModeEntry(c, "purge_all");
    const kMode = getUninstallModeEntry(c, "keep_data");
    uModeHtml = `<div class="uninstall-mode-box">
      <div class="um-head"><span class="um-label">卸载方式</span><span class="um-desc" id="umdesc-${id}">${escapeHtml(uninstallModeDescription(c, curMode))}</span></div>
      <div class="um-seg">
        <button type="button" class="um-btn${curMode === "keep_data" ? " active" : ""}" data-act="umode" data-mode="keep_data">保留用户数据（仅删 App 与缓存 · ${formatShort(Number(kMode.size_bytes || 0))}）</button>
        <button type="button" class="um-btn${curMode === "purge_all" ? " active" : ""}" data-act="umode" data-mode="purge_all">一键全部删除（含残留 · ${formatShort(Number(pMode.size_bytes || 0))}）</button>
      </div>
    </div>`;
  }
  return `<div class="row${checked ? " is-checked" : ""}${wl ? " is-protected" : ""}${done ? " is-done" : ""}" id="card-${id}" data-id="${id}">
    <button type="button" class="check" role="checkbox" aria-checked="${checked || done}" aria-label="选择：${title}" data-act="toggle"${wl || done ? " disabled" : ""}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 12.5l3.8 3.8 7.2-8.3"/></svg>
    </button>
    <button type="button" class="row-main" aria-expanded="false" aria-controls="detail-${id}" data-act="expand">
      <span class="app-icon tint-${wl ? "orange" : act.tint}">${icon(wl ? "shield" : act.icon)}</span>
      <span class="row-text">
        <span class="row-title" title="${title}">${title}</span>
        <span class="row-sub"><span class="chip ${tier.cls}">${tier.label} ${tier.desc}</span>${statusChip}${g ? `<span class="chip">${escapeHtml(poolTitle(g))}</span>` : ""}<span class="cat">${escapeHtml(categoryLabel(c) || act.label)}</span></span>
      </span>
      ${sizeHtml}
      <span class="chev">${icon("chevron")}</span>
    </button>
    <div class="row-detail" id="detail-${id}" hidden><div class="row-detail-inner">
      ${c.evidence ? `<p class="evidence">${escapeHtml(c.evidence)}</p>` : ""}
      ${uModeHtml}
      ${pathsHtml}
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

function setUninstallMode(id, mode) {
  const c = candById[id];
  const norm = normalizeUninstallMode(mode);
  if (!c || !norm || !getUninstallModeEntry(c, norm)) return;
  state.uninstallModes[id] = norm;
  saveState();
  const card = document.getElementById("card-" + id);
  if (card) {
    card.querySelectorAll('.um-btn[data-act="umode"]').forEach(btn => {
      btn.classList.toggle("active", normalizeUninstallMode(btn.getAttribute("data-mode")) === norm);
    });
    const descEl = document.getElementById("umdesc-" + id);
    if (descEl) descEl.textContent = uninstallModeDescription(c, norm);
    const sizeEl = card.querySelector(".row-size:not(.badge)");
    if (sizeEl) sizeEl.textContent = formatBytes(getReclaimableBytes(c));
    const pathBox = card.querySelector(".path-line");
    if (pathBox) {
      pathBox.innerHTML = activePathsOf(c).map(p => `<code>${escapeHtml(p)}</code>`).join("");
    }
  }
  updateRow(c, false);
  updateSelectionTotals(true);
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
  const selectable = visibleItems().filter(c => !isWl(c) && !isDone(c));
  if (!selectable.length) return;
  const allOn = selectable.every(c => state.checked[c.id]);
  let n = 0;
  if (allOn) {
    selectable.forEach(c => { if (state.checked[c.id]) { state.checked[c.id] = false; n++; } });
    saveState();
    renderCandidates();
    showToast(`本页已取消勾选 ${n} 项`);
  } else {
    selectable.forEach(c => { if (!state.checked[c.id]) { state.checked[c.id] = true; n++; } });
    saveState();
    renderCandidates();
    showToast(`本页又选中了 ${n} 项`);
  }
}
function inQuickGroup(c, key) {
  if (isDone(c) || isWl(c)) return false;
  const isMaint = getReclaimableBytes(c) === 0;
  if (key === "opt") return isMaint;
  if (isMaint) return false;
  if (key === "tierA") return tierOf(c) === "A";
  if (key === "tierB") return tierOf(c) === "B";
  if (key === "tierC") return tierOf(c) === "C";
  return false;
}
function syncQuickMenu() {
  const menu = document.getElementById("quick-menu");
  if (!menu) return;
  const groups = ["tierA", "tierB", "tierC", "opt"];
  let totalAll = 0;
  let checkedAll = 0;
  groups.forEach(key => {
    const items = CANDS.filter(c => inQuickGroup(c, key));
    const total = items.length;
    const checkedItems = items.filter(c => state.checked[c.id]);
    const checked = checkedItems.length;
    const totalBytes = items.reduce((s, c) => s + getReclaimableBytes(c), 0);
    totalAll += total;
    checkedAll += checked;
    const row = menu.querySelector(`.qm-row[data-quick="${key}"]`);
    if (row) {
      row.disabled = total === 0;
      const stateStr = total > 0 && checked === total ? "true" : (checked > 0 ? "mixed" : "false");
      row.setAttribute("aria-checked", stateStr);
    }
    const meta = document.getElementById("qm-meta-" + key);
    if (meta) {
      if (total === 0) {
        meta.textContent = "0 项";
      } else if (key === "opt") {
        meta.textContent = `${checked}/${total} 项`;
      } else {
        meta.textContent = `${checked}/${total} · ${formatShort(totalBytes)}`;
      }
    }
  });
  const btnAll = document.getElementById("qm-select-all");
  const btnClear = document.getElementById("qm-clear-all");
  if (btnAll) {
    const allOn = totalAll > 0 && checkedAll === totalAll;
    btnAll.classList.toggle("is-all-on", allOn);
    btnAll.textContent = allOn ? "已全选全部 ✓" : `全选全部 (${totalAll})`;
    btnAll.disabled = totalAll === 0;
  }
  if (btnClear) {
    btnClear.disabled = checkedAll === 0;
  }
}
function toggleQuickGroup(key) {
  const items = CANDS.filter(c => inQuickGroup(c, key));
  if (!items.length) return;
  const allChecked = items.every(c => state.checked[c.id]);
  const nextState = !allChecked;
  items.forEach(c => { state.checked[c.id] = nextState; });
  saveState();
  const labels = { tierA: "A 级", tierB: "B 级", tierC: "C 级", opt: "优化、整理和更新" };
  refreshAfterBulk(`${nextState ? "已勾选" : "已取消"}全部 ${labels[key] || key}（${items.length} 项）`);
  syncQuickMenu();
  const chk = document.querySelector(`#quick-menu .qm-row[data-quick="${key}"] .qm-check`);
  if (chk) popScale(chk, popSprings, 0.78);
}
function selectAllCandidates() {
  let count = 0;
  CANDS.forEach(c => {
    if (!isDone(c) && !isWl(c)) {
      state.checked[c.id] = true;
      count++;
    }
  });
  saveState();
  refreshAfterBulk(`已全选全部 ${count} 项建议`);
  syncQuickMenu();
}
function clearSelections() {
  state.checked = {};
  saveState();
  refreshAfterBulk("已清空选择");
  syncQuickMenu();
}
function resetSavedState() {
  safeStorageRemove(STORAGE_KEY);
  state = loadState();
  expandedRows.clear();
  toggleQuickMenu(false);
  refreshAfterBulk("已恢复到这份报告的初始勾选");
  syncQuickMenu();
}
function refreshAfterBulk(message) {
  if (currentView === "overview") updateSelectionTotals(true); else renderCandidates();
  popScale(document.getElementById("dock-stat"), popSprings, 0.9);
  if (message && !menuOpen) showToast(message);
}

/* ================= Impact bar (workbench) ================= */
const impactSprings = {};
["seg-retained", "seg-reclaim-item", "seg-reclaim-mid"].forEach(id => {
  impactSprings[id] = new Spring(0, SPRING.ring, v => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.width = clamp(v, 0, 100).toFixed(3) + "%";
    el.style.display = v < 0.05 ? "none" : "";
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
  const t = document.getElementById("impact-title");
  if (t) t.textContent = poolTitle(g);
  const sub = document.getElementById("impact-sub");
  const lg = document.getElementById("impact-legend");

  if (m.offline) {
    impactSprings["seg-retained"].to(0);
    impactSprings["seg-reclaim-item"].to(0);
    impactSprings["seg-reclaim-mid"].to(0);
    if (sub) {
      sub.textContent = m.offlineNote
        ? `未挂载 / 离线 · ${m.offlineNote}`
        : "未挂载 / 离线（容量与水位数据暂不可用）";
    }
    if (lg) {
      lg.innerHTML = `<span><i class="dot"></i>该存储卷当前<b>未挂载 / 离线</b>${m.offlineNote ? `（${escapeHtml(m.offlineNote)}）` : ""}，不计入空间水位占比</span>`;
    }
    return;
  }

  const total = m.total;
  const saved = m.cleanedBytes;
  let focus = focusedItemId ? candById[focusedItemId] : null;
  if (!focus || focus.gauge_id !== g.id || isWl(focus) || !(getReclaimableBytes(focus) > 0)) focus = null;
  if (!focus && lastToggledId) {
    const lt = candById[lastToggledId];
    if (lt && lt.gauge_id === g.id && isChecked(lt) && getReclaimableBytes(lt) > 0) focus = lt;
  }
  const preview = Boolean(focus && !isChecked(focus) && !isDone(focus));
  const focusReclaim = focus ? getReclaimableBytes(focus) : 0;
  const focusBytes = focus ? Math.min(preview ? m.retainedBytes : saved, focusReclaim) : 0;
  const retained = Math.max(0, m.retainedBytes - (preview ? focusBytes : 0));
  const otherSaved = Math.max(0, saved - (preview ? 0 : focusBytes));
  const barPct = b => b > 0 ? Math.max(1.5, pct(b, total)) : 0;
  impactSprings["seg-retained"].to(barPct(retained));
  impactSprings["seg-reclaim-item"].to(barPct(focusBytes));
  impactSprings["seg-reclaim-mid"].to(barPct(otherSaved));
  const focusEl = document.getElementById("seg-reclaim-item");
  if (focusEl) focusEl.classList.toggle("preview", preview);
  if (sub) {
    sub.textContent = saved > 0
      ? `清理后水位 ${fmtPct(m.beforePct)} → ${fmtPct(m.afterPct)}，可用空间变成 ${formatBytes(m.freeAfterBytes)}`
      : `当前水位 ${fmtPct(m.afterPct)}，可用 ${formatBytes(m.freeAfterBytes)}`;
  }
  if (lg) {
    const focusLabel = focus ? (preview ? "这一项（未勾选）" : "这一项") : "";
    lg.innerHTML = `<span><i class="dot ring-used"></i>清理后已用 <b>${formatBytes(retained)}</b></span>` +
      (focus ? `<span><i class="dot ring-focus"></i>${focusLabel} <b>${formatBytes(focusBytes)}</b></span>` : "") +
      `<span><i class="dot ring-clean"></i>${focus && !preview ? "其他已选" : "预计释放"} <b>${formatBytes(otherSaved)}</b></span>` +
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
  const doneTotal = scope.filter(c => isDone(c)).length;
  const sel = selectionSummary(c => matchesView(c, currentView));
  const sub = document.getElementById("list-sub");
  if (sub) {
    const doneNote = doneTotal > 0 ? `（已执行 <b>${doneTotal}</b> 条）` : "";
    sub.innerHTML = currentView === "whitelisted"
      ? `共 <b>${scope.length}</b> 项受保护。执行时会被 preflight 强制跳过，新加入的会写进导出指令。`
      : `共 <b>${scope.length}</b> 条建议${doneNote}，已选 <b>${sel.count}</b> 条${sel.bytes > 0 ? `，预计释放 <b>${formatBytes(sel.bytes)}</b>` : ""}。点开一行可以看依据、写备注或加入白名单。`;
  }
  const selBtn = document.getElementById("select-visible-btn");
  if (selBtn && currentView !== "whitelisted") {
    const selectable = visibleItems().filter(c => !isWl(c) && !isDone(c));
    const allOn = selectable.length > 0 && selectable.every(c => state.checked[c.id]);
    selBtn.disabled = selectable.length === 0;
    selBtn.textContent = allOn ? "取消本页" : "全选本页";
  }
  ["A", "B", "C"].forEach(t => {
    const el = document.getElementById("ghead-" + t);
    if (!el) return;
    const inTier = c => matchesView(c, currentView) && tierOf(c) === t && (currentAction === "all" || inferActionType(c) === currentAction);
    const tierItems = CANDS.filter(inTier);
    const doneCnt = tierItems.filter(c => isDone(c)).length;
    const selectableCnt = tierItems.filter(c => !isDone(c) && !isWl(c)).length;
    const s = selectionSummary(inTier);
    if (selectableCnt === 0 && doneCnt > 0) {
      el.textContent = `已执行 ${doneCnt} 项`;
    } else if (doneCnt > 0) {
      el.textContent = `已选 ${s.count} 项${s.bytes ? "，" + formatBytes(s.bytes) : ""} · 已执行 ${doneCnt} 项`;
    } else {
      el.textContent = `已选 ${s.count} 项${s.bytes ? "，" + formatBytes(s.bytes) : ""}`;
    }
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
  if (menuOpen) syncQuickMenu();
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
    syncQuickMenu();
    menu.hidden = false;
    const r = btn.getBoundingClientRect();
    const w = menu.offsetWidth;
    const left = clamp(r.left + r.width / 2 - w / 2, 10, window.innerWidth - w - 10);
    menu.style.left = left + "px";
    menu.style.bottom = (window.innerHeight - r.top + 10) + "px";
    menu.style.transformOrigin = (r.left + r.width / 2 - left) + "px 100%";
    menuSpring.to(1, SPRING.sheet);
    const first = menu.querySelector("button:not(:disabled)");
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
    toggleQuickMenu(false);
    return;
  }
  if (resetArmed) disarmReset();
  if (action === "tierA" || action === "tierB" || action === "tierC" || action === "opt") {
    toggleQuickGroup(action);
    return;
  }
  if (action === "all") {
    selectAllCandidates();
    return;
  }
  if (action === "clear") {
    clearSelections();
    return;
  }
}

/* ================= Execution prompt sheet (grouped by uninstall mode) ================= */
const GOVERNANCE_DOC_RE = /^(?:agents\.md|claude\.md|gemini\.md|review\.md|contributing\.md|readme(?:\.[a-z0-9_-]+)?)$/i;

function isRefusedQuarantinePath(rawPath) {
  const p = String(rawPath || "").trim();
  if (!p) return true;
  if (!(p.startsWith("/") || p.startsWith("~/"))) return true;
  if (/(?:^|\/)\.git(?:\/|$)/.test(p)) return true;
  const trimmed = p.replace(/\/+$/, "");
  const segs = trimmed.split("/");
  const base = segs[segs.length - 1] || "";
  const parent = segs[segs.length - 2] || "";
  if (GOVERNANCE_DOC_RE.test(base)) return true;
  if (/\.plist$/i.test(base) && (parent === "LaunchAgents" || parent === "LaunchDaemons")) return true;
  return false;
}

function isQuarantineEligibleItem(c) {
  if (!c) return false;
  if (c.quarantine_allowed === false) return false;
  const cat = String(c.category || "").trim().toLowerCase();
  const kind = String(c.kind || "").trim().toLowerCase();
  if (cat.includes("worktree") || kind === "worktree") return false;
  if (NON_RECLAIMABLE_CATEGORIES.has(cat)) return false;
  const isApp = Boolean(c.uninstall_modes || cat === "app_uninstall");
  if (!isApp) {
    const action = String(c.action_type || "cleanup").trim().toLowerCase();
    if (action !== "cleanup") return false;
    if (getReclaimableBytes(c) <= 0) return false;
  }
  if (c.domain && c.domain !== "mac" && c.domain !== "projects" && c.domain !== "repos") return false;
  const paths = activePathsOf(c);
  if (!paths.length) return false;
  if (paths.some(isRefusedQuarantinePath)) return false;
  return true;
}

function formatCommandGroupBlock(comment, ids, modeCli, suffixSlug) {
  const idStr = ids.join(",");
  const modeFlag = modeCli ? ` \\\n  --uninstall-mode ${modeCli}` : "";
  return [
    `# ${comment}`,
    `python3 scripts/full_stack_tool.py preflight \\`,
    `  --config config/topology.local.json \\`,
    `  --targets state/current-snapshot.json \\`,
    `  --ids "${idStr}"${modeFlag} \\`,
    `  --output state/preflight-${suffixSlug}.json --overwrite`,
    `python3 scripts/full_stack_tool.py quarantine \\`,
    `  --config config/topology.local.json \\`,
    `  --targets state/current-snapshot.json \\`,
    `  --ids "${idStr}"${modeFlag} \\`,
    `  --dry-run \\`,
    `  --output state/quarantine-${suffixSlug}-plan.json --overwrite`
  ].join("\n");
}

function buildExecutionPrompt() {
  const selected = [];
  const newlyWhitelisted = [];
  let totalReclaim = 0;

  CANDS.forEach(c => {
    const note = (state.notes[c.id] || "").trim();
    const isAppUninstall = Boolean(c.uninstall_modes || c.category === "app_uninstall");
    const uMode = isAppUninstall ? (uninstallModeOf(c) || normalizeUninstallMode(c.selected_uninstall_mode) || "keep_data") : "";
    const paths = activePathsOf(c);
    if (state.whitelisted[c.id] && !c.whitelisted) {
      newlyWhitelisted.push({ id: c.id, title: stripLeadingEmoji(c.title), path: c.path || (paths[0] || ""), note });
    } else if (state.checked[c.id] && c.status !== "done" && !state.whitelisted[c.id]) {
      const reclaim = getReclaimableBytes(c);
      totalReclaim += reclaim;
      selected.push({
        raw: c,
        id: c.id,
        title: stripLeadingEmoji(c.title),
        path: c.path || "",
        paths,
        uMode,
        reclaim,
        refBytes: getReferenceBytes(c),
        quarantinable: isQuarantineEligibleItem(c),
        note
      });
    }
  });

  const lines = [
    "请按我在全栈巡检看板上的勾选执行清理与优化（动手前请先用 `full_stack_tool.py preflight` 复查并发活跃状态）：",
    ""
  ];
  if (selected.length === 0) {
    lines.push("- （本次未勾选待执行项）");
  } else {
    const reclaimHeader = totalReclaim > 0 ? `，预计释放 ${formatBytes(totalReclaim)}` : "";
    lines.push(`### 已勾选执行项（共 ${selected.length} 项${reclaimHeader}）`);
    selected.forEach(item => {
      const pathPart = item.paths && item.paths.length > 1
        ? ` (${item.paths.map(p => `\`${p}\``).join(", ")})`
        : (item.path ? ` (\`${item.path}\`)` : "");
      let modePart = "";
      if (item.uMode) {
        const meta = UNINSTALL_MODE_DEFAULTS[item.uMode] || UNINSTALL_MODE_DEFAULTS.keep_data;
        modePart = ` [卸载模式: \`--uninstall-mode ${meta.cli}\` (${meta.shortLabel}：${uninstallModeDescription(item.raw, item.uMode)})]`;
      }
      const sizeNote = item.reclaim > 0
        ? ` [预计释放 ${formatBytes(item.reclaim)}]`
        : (item.refBytes > 0 ? ` [参考体量 ${formatBytes(item.refBytes)}，非直接删文件]` : "");
      const notePart = item.note ? ` —— **微调备注：${item.note}**` : "";
      lines.push(`- [x] \`${item.id}\`: ${item.title}${pathPart}${sizeNote}${modePart}${notePart}`);
    });

    const regularCleanup = selected.filter(x => x.quarantinable && !x.uMode);
    const keepDataApps = selected.filter(x => x.quarantinable && x.uMode === "keep_data");
    const purgeAllApps = selected.filter(x => x.quarantinable && x.uMode === "purge_all");
    const nonQuarantine = selected.filter(x => !x.quarantinable);

    const cmdBlocks = [];
    if (regularCleanup.length) {
      cmdBlocks.push(formatCommandGroupBlock(
        `常规空间清理项（${regularCleanup.length} 项）`,
        regularCleanup.map(x => x.id),
        "",
        "cleanup"
      ));
    }
    if (keepDataApps.length) {
      cmdBlocks.push(formatCommandGroupBlock(
        `应用卸载 · 保留用户数据（${keepDataApps.length} 项：${keepDataApps.map(x => x.id).join(", ")}）`,
        keepDataApps.map(x => x.id),
        "keep-data",
        "keep-data"
      ));
    }
    if (purgeAllApps.length) {
      cmdBlocks.push(formatCommandGroupBlock(
        `应用卸载 · 一键全部删除含残留（${purgeAllApps.length} 项：${purgeAllApps.map(x => x.id).join(", ")}）`,
        purgeAllApps.map(x => x.id),
        "purge-all",
        "purge-all"
      ));
    }

    if (cmdBlocks.length) {
      lines.push("");
      lines.push("### 分组安全复查与隔离预演命令（按卸载模式独立执行，避免全局参数互相覆盖）");
      lines.push("```bash");
      lines.push(cmdBlocks.join("\n\n"));
      lines.push("```");
    }
    if (nonQuarantine.length) {
      lines.push("");
      lines.push(`### 非隔离区治理 / 优化 / 更新项（共 ${nonQuarantine.length} 项，不走 quarantine 搬移）`);
      nonQuarantine.forEach(item => {
        lines.push(`- \`${item.id}\`: ${item.title}`);
      });
    }
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
    else if (act === "umode") setUninstallMode(id, actEl.getAttribute("data-mode"));
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
  poolSel.innerHTML = GAUGES.map(g => {
    const offTag = isGaugeOffline(g) ? "（未挂载 / 离线）" : "";
    return `<option value="${escapeHtml(g.id)}">${escapeHtml(poolTitle(g) + offTag)}</option>`;
  }).join("");
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
  try { renderEnvAndPeripherals(); } catch (e) {}
  try { renderDiff(); } catch (e) {}
  let start = "overview";
  try { const h = (location.hash || "").replace("#", ""); if (VIEWS.indexOf(h) >= 0) start = h; } catch (e) {}
  currentView = "";
  navigate(start, { keepScroll: true });
  updateSelectionTotals(false);
  requestAnimationFrame(() => placeNavPill(false));
}

bootstrapDashboard();
