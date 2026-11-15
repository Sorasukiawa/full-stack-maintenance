from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import render_dashboard  # noqa: E402


NODE_HARNESS_JS = textwrap.dedent(
    r"""
    const fs = require("fs");
    const vm = require("vm");

    const htmlPath = process.argv[2];
    const scenario = process.argv[3] || "default";
    const html = fs.readFileSync(htmlPath, "utf8");

    const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
    if (!scriptMatch) {
      throw new Error("No inline <script> found in rendered HTML");
    }
    const scriptCode = scriptMatch[1];

    // Minimal deterministic DOM & Canvas stub sufficient to boot and interact with app.js
    class ClassList {
      constructor(el) { this.el = el; this.set = new Set(); }
      _syncFromAttr(cls) {
        this.set = new Set(String(cls || "").split(/\s+/).filter(Boolean));
      }
      _writeAttr() {
        this.el._className = Array.from(this.set).join(" ");
      }
      add(...names) { names.forEach(n => { if (n) this.set.add(n); }); this._writeAttr(); }
      remove(...names) { names.forEach(n => this.set.delete(n)); this._writeAttr(); }
      toggle(name, force) {
        const on = force === undefined ? !this.set.has(name) : Boolean(force);
        if (on) this.set.add(name); else this.set.delete(name);
        this._writeAttr();
        return on;
      }
      contains(name) { return this.set.has(name); }
    }

    const elementsById = new Map();
    const allElements = [];

    function decodeEntities(str) {
      return String(str || "")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, "&");
    }

    function stripTags(rawHtml) {
      return decodeEntities(String(rawHtml || "").replace(/<[^>]*>/g, ""));
    }

    class MockElement {
      constructor(tagName = "div", id = "") {
        this.tagName = String(tagName || "div").toUpperCase();
        this._id = "";
        this._className = "";
        this.classList = new ClassList(this);
        this.attributes = new Map();
        this.dataset = {};
        this.style = { setProperty() {}, getPropertyValue() { return ""; } };
        this.children = [];
        this.parentElement = null;
        this.hidden = false;
        this.disabled = false;
        this.value = "";
        this._innerHTML = "";
        this._textContent = "";
        this.listeners = new Map();
        this.offsetLeft = 0;
        this.offsetTop = 0;
        this.offsetWidth = 180;
        this.offsetHeight = 36;
        this.scrollHeight = 140;
        this.clientWidth = 320;
        this.width = 320;
        this.height = 320;
        if (id) this.id = id;
        allElements.push(this);
      }
      get id() { return this._id; }
      set id(v) {
        if (this._id && elementsById.get(this._id) === this) elementsById.delete(this._id);
        this._id = String(v || "");
        if (this._id) elementsById.set(this._id, this);
      }
      get className() { return this._className; }
      set className(v) {
        this._className = String(v || "");
        this.classList._syncFromAttr(this._className);
      }
      get firstChild() { return this.children[0] || (this._innerHTML ? {} : null); }
      get options() {
        return this.children.filter(c => c.tagName === "OPTION");
      }
      get innerHTML() { return this._innerHTML; }
      set innerHTML(val) {
        this._innerHTML = String(val == null ? "" : val);
        this._textContent = stripTags(this._innerHTML);
        this.children = [];
        parseFragmentInto(this, this._innerHTML);
      }
      get textContent() { return this._textContent; }
      set textContent(val) {
        this._textContent = String(val == null ? "" : val);
        this._innerHTML = this._textContent;
        this.children = [];
      }
      appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
      }
      replaceChildren(...nodes) {
        this.children = [];
        nodes.forEach(n => this.appendChild(n));
        this._textContent = nodes.map(n => n.textContent || "").join("");
      }
      insertAdjacentHTML(_pos, htmlFrag) {
        parseFragmentInto(this, htmlFrag);
      }
      setAttribute(k, v) {
        const val = String(v);
        this.attributes.set(k, val);
        if (k === "id") this.id = val;
        else if (k === "class") this.className = val;
        else if (k === "hidden") this.hidden = true;
        else if (k.startsWith("data-")) {
          const camel = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
          this.dataset[camel] = val;
        }
      }
      removeAttribute(k) {
        this.attributes.delete(k);
        if (k === "hidden") this.hidden = false;
      }
      getAttribute(k) {
        if (k === "id") return this._id || null;
        if (k === "class") return this._className || null;
        return this.attributes.has(k) ? this.attributes.get(k) : null;
      }
      addEventListener(type, fn) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(fn);
      }
      dispatchEvent(ev) {
        const list = this.listeners.get(ev.type) || [];
        ev.target = ev.target || this;
        ev.currentTarget = this;
        list.forEach(fn => fn(ev));
      }
      focus() {}
      select() {}
      scrollIntoView() {}
      contains(node) {
        let cur = node;
        while (cur) {
          if (cur === this) return true;
          cur = cur.parentElement;
        }
        return false;
      }
      getBoundingClientRect() {
        return { left: 100, top: 100, right: 280, bottom: 136, width: 180, height: 36 };
      }
      getContext() {
        return {
          setTransform() {}, clearRect() {}, beginPath() {}, arc() {}, stroke() {},
          save() {}, restore() {}, clip() {}, fill() {},
          createConicGradient() { return { addColorStop() {} }; }
        };
      }
      matches(selector) {
        return matchSimpleSelector(this, selector);
      }
      closest(selector) {
        let cur = this;
        while (cur) {
          if (cur.matches && cur.matches(selector)) return cur;
          cur = cur.parentElement;
        }
        return null;
      }
      querySelectorAll(selector) {
        const out = [];
        const walk = node => {
          node.children.forEach(ch => {
            if (matchSimpleSelector(ch, selector)) out.push(ch);
            walk(ch);
          });
        };
        walk(this);
        return out;
      }
      querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
      }
    }

    function matchSinglePart(el, part) {
      let s = part.trim();
      if (!s) return false;
      s = s.replace(/:not\([^)]*\)/g, "");
      const attrRe = /\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]/g;
      let m;
      while ((m = attrRe.exec(s)) !== null) {
        const attrVal = el.getAttribute(m[1]);
        if (m[2] !== undefined) {
          if (attrVal !== m[2]) return false;
        } else if (attrVal === null) {
          return false;
        }
      }
      const clean = s.replace(attrRe, "");
      const idMatch = clean.match(/#([a-zA-Z0-9_-]+)/);
      if (idMatch && el.id !== idMatch[1]) return false;
      const clsMatches = clean.match(/\.([a-zA-Z0-9_-]+)/g) || [];
      for (const cm of clsMatches) {
        if (!el.classList.contains(cm.slice(1))) return false;
      }
      const tagMatch = clean.match(/^[a-zA-Z0-9]+/);
      if (tagMatch && el.tagName !== tagMatch[0].toUpperCase()) return false;
      return true;
    }

    function matchSimpleSelector(el, selectorGroup) {
      return String(selectorGroup).split(",").some(sel => {
        const parts = sel.trim().split(/\s+/);
        let cur = el;
        for (let i = parts.length - 1; i >= 0; i--) {
          if (!cur) return false;
          if (i === parts.length - 1) {
            if (!matchSinglePart(cur, parts[i])) return false;
            cur = cur.parentElement;
          } else {
            while (cur && !matchSinglePart(cur, parts[i])) cur = cur.parentElement;
            if (!cur) return false;
            cur = cur.parentElement;
          }
        }
        return true;
      });
    }

    function parseAttributes(el, attrStr) {
      const re = /([a-zA-Z0-9_:-]+)(?:="([^"]*)")?/g;
      let m;
      while ((m = re.exec(attrStr)) !== null) {
        const name = m[1];
        if (name === "/") continue;
        el.setAttribute(name, m[2] !== undefined ? decodeEntities(m[2]) : "");
      }
    }

    function parseFragmentInto(parent, rawHtml) {
      // Parse top-level and nested tags enough for IDs, buttons, options, spans, inputs
      const tokenRe = /<(\/?)([a-zA-Z0-9]+)((?:\s+[^<>]*?)?)(\/?)>/g;
      const stack = [parent];
      let lastIndex = 0;
      let m;
      const voidTags = new Set(["META", "IMG", "BR", "HR", "INPUT", "LINK"]);
      while ((m = tokenRe.exec(rawHtml)) !== null) {
        const textChunk = rawHtml.slice(lastIndex, m.index);
        if (textChunk && stack.length > 1) {
          stack[stack.length - 1]._textContent += decodeEntities(textChunk);
        }
        lastIndex = tokenRe.lastIndex;
        const closing = m[1] === "/";
        const tag = m[2].toUpperCase();
        const attrs = m[3] || "";
        const selfClose = m[4] === "/" || voidTags.has(tag);
        if (closing) {
          if (stack.length > 1) stack.pop();
        } else {
          const el = new MockElement(tag);
          parseAttributes(el, attrs);
          stack[stack.length - 1].appendChild(el);
          if (!selfClose) stack.push(el);
        }
      }
      const tail = rawHtml.slice(lastIndex);
      if (tail && stack.length > 1) {
        stack[stack.length - 1]._textContent += decodeEntities(tail);
      }
    }

    const docRoot = new MockElement("html");
    const bodyMatch = html.match(/<body>([\s\S]*?)<script>/);
    const bodyEl = new MockElement("body");
    docRoot.appendChild(bodyEl);
    if (bodyMatch) parseFragmentInto(bodyEl, bodyMatch[1]);

    const storageMap = new Map();
    const localStorageStub = {
      getItem: k => (storageMap.has(k) ? storageMap.get(k) : null),
      setItem: (k, v) => storageMap.set(k, String(v)),
      removeItem: k => storageMap.delete(k)
    };

    const documentStub = {
      documentElement: docRoot,
      body: bodyEl,
      getElementById: id => elementsById.get(id) || null,
      querySelectorAll: sel => docRoot.querySelectorAll(sel),
      querySelector: sel => docRoot.querySelector(sel),
      createElement: tag => new MockElement(tag),
      addEventListener: (t, fn) => docRoot.addEventListener(t, fn),
      execCommand: () => true
    };

    const windowStub = {
      document: documentStub,
      localStorage: localStorageStub,
      innerWidth: 1280,
      innerHeight: 820,
      devicePixelRatio: 2,
      location: { hash: "" },
      history: { replaceState() {} },
      navigator: { clipboard: { writeText: () => Promise.resolve() } },
      matchMedia: q => ({
        matches: q.includes("reduce"),
        addEventListener() {}
      }),
      getComputedStyle: () => ({
        getPropertyValue: name => {
          const map = {
            "color-scheme": "light",
            "--track": "rgba(120,120,128,0.14)",
            "--used": "#aeaeb2",
            "--green": "#34c759",
            "--teal": "#30b0c7",
            "--blue": "#0071e3"
          };
          return map[name] || "";
        }
      }),
      requestAnimationFrame: () => 1,
      cancelAnimationFrame() {},
      scrollTo() {},
      addEventListener() {},
      Event: class Event { constructor(type, opts) { this.type = type; this.bubbles = Boolean(opts && opts.bubbles); } },
      setTimeout: () => 1,
      clearTimeout() {}
    };

    const sandbox = Object.assign({}, windowStub, {
      window: windowStub,
      document: documentStub,
      localStorage: localStorageStub,
      navigator: windowStub.navigator,
      location: windowStub.location,
      history: windowStub.history,
      getComputedStyle: windowStub.getComputedStyle,
      Event: windowStub.Event
    });
    vm.createContext(sandbox);
    vm.runInContext(scriptCode, sandbox, { filename: "rendered-dashboard.js" });

    // Expose helpers via evaluation inside the same VM context
    function evalInApp(expr) {
      return vm.runInContext(expr, sandbox);
    }

    const out = {};
    if (scenario === "categories") {
      out.labels = evalInApp(`CANDS.map(c => ({ id: c.id, label: categoryLabel(c), action: inferActionType(c) }))`);
    } else if (scenario === "offline") {
      const metrics = evalInApp(`computeAllMetrics()`);
      evalInApp(`selectRingPool("nas-unmounted")`);
      const singleOffline = {
        badgeText: documentStub.getElementById("ring-active-badge").textContent,
        badgeClass: documentStub.getElementById("ring-active-badge").className,
        centerNum: documentStub.getElementById("ring-center-num").textContent,
        centerWater: documentStub.getElementById("ring-center-water").textContent,
        descHtml: documentStub.getElementById("ring-active-desc").innerHTML,
        miniPct: documentStub.getElementById("mring-pct-nas-unmounted").textContent,
        miniMeta: documentStub.getElementById("mring-meta-nas-unmounted").textContent
      };
      evalInApp(`selectRingPool("_all")`);
      const allView = {
        badgeText: documentStub.getElementById("ring-active-badge").textContent,
        badgeClass: documentStub.getElementById("ring-active-badge").className,
        mountHtml: documentStub.getElementById("ring-active-mount").innerHTML,
        descHtml: documentStub.getElementById("ring-active-desc").innerHTML
      };
      out.metrics = metrics;
      out.singleOffline = singleOffline;
      out.allView = allView;
    } else if (scenario === "all_offline") {
      const metrics = evalInApp(`computeAllMetrics()`);
      evalInApp(`selectRingPool("_all")`);
      out.aggOffline = metrics.agg.offline;
      out.badgeText = documentStub.getElementById("ring-active-badge").textContent;
      out.badgeClass = documentStub.getElementById("ring-active-badge").className;
      out.centerNum = documentStub.getElementById("ring-center-num").textContent;
      out.centerWater = documentStub.getElementById("ring-center-water").textContent;
    } else if (scenario === "reclaimable") {
      evalInApp(`selectAllCandidates()`);
      evalInApp(`navigate("all")`);
      out.perItem = evalInApp(`CANDS.map(c => ({ id: c.id, reclaim: getReclaimableBytes(c), ref: getReferenceBytes(c), impact: impactText(c) }))`);
      out.summary = evalInApp(`selectionSummary()`);
      out.poolMetrics = evalInApp(`computePoolRingMetrics(GAUGES[0])`);
      out.dockGib = documentStub.getElementById("selected-gib").textContent;
      out.candidateListHtml = documentStub.getElementById("candidate-list").innerHTML;
      evalInApp(`navigate("overview")`);
      out.overviewListsHtml = documentStub.getElementById("ring-detailed-lists").innerHTML;
    } else if (scenario === "dual_uninstall") {
      evalInApp(`navigate("mac")`);
      const descAlphaInit = documentStub.getElementById("umdesc-app-alpha").textContent;
      const descBetaInit = documentStub.getElementById("umdesc-app-beta").textContent;
      evalInApp(`toggleItem("build-1", true)`);
      evalInApp(`toggleItem("app-alpha", true)`);
      evalInApp(`toggleItem("app-beta", true)`);
      evalInApp(`toggleItem("git-pack-1", true)`);
      evalInApp(`setUninstallMode("app-alpha", "keep_data")`);
      evalInApp(`setUninstallMode("app-beta", "purge-all")`);
      const descBetaAfter = documentStub.getElementById("umdesc-app-beta").textContent;
      const reclaimAlpha = evalInApp(`getReclaimableBytes(candById["app-alpha"])`);
      const reclaimBeta = evalInApp(`getReclaimableBytes(candById["app-beta"])`);
      const prompt = evalInApp(`buildExecutionPrompt()`);
      out.descAlphaInit = descAlphaInit;
      out.descBetaInit = descBetaInit;
      out.descBetaAfter = descBetaAfter;
      out.reclaimAlpha = reclaimAlpha;
      out.reclaimBeta = reclaimBeta;
      out.prompt = prompt;
    } else if (scenario === "env_periph") {
      const sec = documentStub.getElementById("env-periph-section");
      out.hidden = sec.hidden;
      out.summary = documentStub.getElementById("env-periph-summary").textContent;
      out.contentHtml = documentStub.getElementById("env-periph-content").innerHTML;
    }

    process.stdout.write(JSON.stringify(out));
    """
)


class DashboardUIModularizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.node_bin = shutil.which("node")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_in_node(self, snapshot: dict, scenario: str, diff: dict | None = None) -> dict:
        if not self.node_bin:
            self.skipTest("node binary not available")
        html_path = self.root / f"dash-{scenario}.html"
        html_text = render_dashboard.render_dashboard_html(snapshot, diff=diff)
        html_path.write_text(html_text, encoding="utf-8")
        harness_path = self.root / "harness.js"
        harness_path.write_text(NODE_HARNESS_JS, encoding="utf-8")
        proc = subprocess.run(
            [self.node_bin, str(harness_path), str(html_path), scenario],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            self.fail(f"Node runtime execution failed ({proc.returncode}):\n{proc.stderr}")
        return json.loads(proc.stdout)

    def test_split_assets_inline_assembly_csp_and_escaping(self) -> None:
        assets_dir = SCRIPTS_DIR / "dashboard_assets"
        self.assertTrue((assets_dir / "template.html").is_file())
        self.assertTrue((assets_dir / "styles.css").is_file())
        self.assertTrue((assets_dir / "app.js").is_file())

        if self.node_bin:
            chk = subprocess.run(
                [self.node_bin, "--check", str(assets_dir / "app.js")],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(chk.returncode, 0, chk.stderr)

        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "status": "ok",
                    "total_bytes": 100 * 1024**3,
                    "used_bytes": 40 * 1024**3,
                    "free_bytes": 60 * 1024**3,
                }
            ],
            "candidates": [
                {
                    "id": "xss-check",
                    "title": "</script><!--<script>alert(1)</script> __TITLE__ __INLINE_SCRIPT__\u2028",
                    "size_bytes": 1024,
                }
            ],
        }
        html_text = render_dashboard.render_html(snapshot, title="<script>标题</script>")
        self.assertEqual(html_text.count("<script>"), 1)
        self.assertEqual(html_text.count("</script>"), 1)
        self.assertIn("Content-Security-Policy", html_text)
        self.assertIn("default-src 'none'", html_text)
        self.assertIn("connect-src 'none'", html_text)
        self.assertNotRegex(html_text, r'<script[^>]+src=')
        self.assertNotRegex(html_text, r'<link[^>]+href=')
        self.assertIn("&lt;script&gt;标题&lt;/script&gt;", html_text)
        self.assertIn("__TITLE__", html_text)
        self.assertIn("__INLINE_SCRIPT__", html_text)
        self.assertNotIn("\u2028", html_text)

        # Overwrite guard returns exit code 2
        snap_file = self.root / "snap.json"
        out_file = self.root / "out.html"
        snap_file.write_text(json.dumps(snapshot), encoding="utf-8")
        self.assertEqual(render_dashboard.main(["--input", str(snap_file), "--output", str(out_file)]), 0)
        self.assertEqual(render_dashboard.main(["--input", str(snap_file), "--output", str(out_file)]), 2)
        self.assertEqual(
            render_dashboard.main(["--input", str(snap_file), "--output", str(out_file), "--overwrite"]),
            0,
        )

    def test_category_labels_mapping_and_unknown_fallback(self) -> None:
        categories = [
            ("c1", "git_pack_bloat"),
            ("c2", "governance_doc"),
            ("c3", "git_bloat"),
            ("c4", "governance_drift"),
            ("c5", "app_orphan"),
            ("c6", "app_uninstall"),
            ("c7", "system_cache"),
            ("c8", "launch_item"),
            ("c9", "app_update"),
            ("c10", "env_update"),
            ("c11", "peripheral_firmware"),
            ("c12", "build_artifact"),
            ("c13", "backup_exclusion_drift"),
            ("c14", "custom_nvme_cache"),
            ("c15", "pve-orphan_snapshot"),
            ("c16", "自定义中文类别"),
        ]
        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [],
            "candidates": [{"id": cid, "category": cat, "title": cid, "size_bytes": 0} for cid, cat in categories],
        }
        res = self._run_in_node(snapshot, "categories")
        by_id = {x["id"]: x["label"] for x in res["labels"]}

        self.assertEqual(by_id["c1"], "Git 对象膨胀")
        self.assertEqual(by_id["c2"], "治理与交接文档")
        self.assertEqual(by_id["c3"], "Git 仓库膨胀")
        self.assertEqual(by_id["c4"], "工程规则缺失")
        self.assertEqual(by_id["c5"], "卸载残留清理")
        self.assertEqual(by_id["c6"], "不常用应用卸载")
        self.assertEqual(by_id["c7"], "全局工具纯缓存")
        self.assertEqual(by_id["c8"], "开机启动项检查")
        self.assertEqual(by_id["c9"], "应用版本更新")
        self.assertEqual(by_id["c10"], "开发环境与依赖")
        self.assertEqual(by_id["c11"], "外设与存储固件")
        self.assertEqual(by_id["c12"], "可重建构建产物")
        self.assertEqual(by_id["c13"], "备份排除漏排")

        # Unknown snake_case / kebab-case keys must be formatted gracefully without raw underscores
        self.assertNotIn("_", by_id["c14"])
        self.assertIn("Custom NVMe Cache", by_id["c14"])
        self.assertNotIn("_", by_id["c15"])
        self.assertIn("PVE Orphan Snapshot", by_id["c15"])
        self.assertEqual(by_id["c16"], "自定义中文类别")

    def test_offline_storage_gauges_never_show_zero_ok_or_pollute_all(self) -> None:
        gib = 1024**3
        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "mount_path": "/System/Volumes/Data",
                    "status": "ok",
                    "total_bytes": 100 * gib,
                    "used_bytes": 60 * gib,
                    "free_bytes": 40 * gib,
                    "warn_used_pct": 80.0,
                },
                {
                    "id": "nas-unmounted",
                    "label": "NAS Media Share",
                    "mount_path": "/Volumes/Data",
                    "status": "unavailable",
                    "total_bytes": 0,
                    "used_bytes": 0,
                    "free_bytes": 0,
                    "note": "NAS /Volumes/Data 当前未挂载",
                },
                {
                    "id": "pve-remote",
                    "label": "PVE LVM Thin",
                    "mount_path": "pve:local-lvm",
                    "status": "offline",
                    "total_bytes": 500 * gib,
                    "used_bytes": 100 * gib,
                    "free_bytes": 400 * gib,
                    "note": "节点离线休眠",
                },
            ],
            "candidates": [],
        }
        res = self._run_in_node(snapshot, "offline")
        single = res["singleOffline"]
        self.assertEqual(single["badgeText"], "未挂载 / 离线")
        self.assertIn("offline", single["badgeClass"])
        self.assertNotIn("状态良好", single["badgeText"])
        self.assertNotIn("0%", single["miniPct"])
        self.assertEqual(single["miniPct"], "离线")
        self.assertNotIn("0%", single["centerWater"])
        self.assertIn("NAS /Volumes/Data 当前未挂载", single["descHtml"])
        self.assertIn("NAS /Volumes/Data 当前未挂载", single["miniMeta"])

        # _all aggregation must ONLY sum online healthy pools (100 GiB, 60% used) and report 2 offline pools
        agg = res["metrics"]["agg"]
        self.assertFalse(agg["offline"])
        self.assertEqual(agg["onlineCount"], 1)
        self.assertEqual(agg["offlineCount"], 2)
        self.assertEqual(agg["total"], 100 * gib)
        self.assertEqual(agg["beforeUsedBytes"], 60 * gib)
        self.assertAlmostEqual(agg["afterPct"], 60.0, places=2)
        self.assertIn("2", res["allView"]["mountHtml"])
        self.assertIn("未挂载 / 离线", res["allView"]["descHtml"])

        # When ALL pools are offline, _all summary ring itself displays offline state
        all_offline_snap = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [
                {"id": "nas-1", "label": "NAS 1", "status": "unavailable", "total_bytes": 0, "note": "未挂载"},
                {"id": "nas-2", "label": "NAS 2", "status": "ok", "total_bytes": 0, "note": "容量为 0"},
            ],
            "candidates": [],
        }
        res_all_off = self._run_in_node(all_offline_snap, "all_offline")
        self.assertTrue(res_all_off["aggOffline"])
        self.assertEqual(res_all_off["badgeText"], "未挂载 / 离线")
        self.assertIn("offline", res_all_off["badgeClass"])
        self.assertNotIn("0%", res_all_off["centerWater"])

    def test_reclaimable_bytes_eliminates_inflation_and_double_counting(self) -> None:
        gib = 1024**3
        mib = 1024**2
        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "status": "ok",
                    "total_bytes": 100 * gib,
                    "used_bytes": 50 * gib,
                    "free_bytes": 50 * gib,
                    "warn_used_pct": 85.0,
                }
            ],
            "candidates": [
                {
                    "id": "build-same-path",
                    "domain": "mac",
                    "category": "build_artifact",
                    "tier": "A",
                    "title": "可重建构建产物：demo/target",
                    "path": "/tmp/demo/target",
                    "size_bytes": 10 * gib,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "drift-same-path",
                    "domain": "mac",
                    "category": "backup_exclusion_drift",
                    "tier": "B",
                    "title": "备份排除漏排：demo/target",
                    "path": "/tmp/demo/target",
                    "size_bytes": 10 * gib,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "git-pack-repo",
                    "domain": "projects",
                    "category": "git_pack_bloat",
                    "tier": "B",
                    "title": "仓库 Git Pack 较大：demo",
                    "path": "/tmp/demo/.git/objects/pack",
                    "size_bytes": 500 * mib,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "gov-doc-repo",
                    "domain": "projects",
                    "category": "governance_doc",
                    "tier": "B",
                    "title": "补充合入前检查声明",
                    "path": "/tmp/demo/AGENTS.md",
                    "size_bytes": 4096,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "explicit-zero-item",
                    "domain": "mac",
                    "category": "build_artifact",
                    "tier": "B",
                    "title": "显式零可释放条目",
                    "path": "/tmp/demo/other",
                    "size_bytes": 8 * gib,
                    "reclaimable_bytes": 0,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "explicit-partial-item",
                    "domain": "mac",
                    "category": "app_orphan",
                    "tier": "A",
                    "title": "显式部分可释放条目",
                    "path": "/tmp/demo/partial",
                    "size_bytes": 6 * gib,
                    "reclaimable_bytes": 2 * gib,
                    "gauge_id": "mac-data",
                },
            ],
        }
        res = self._run_in_node(snapshot, "reclaimable")
        reclaim_map = {x["id"]: x["reclaim"] for x in res["perItem"]}
        self.assertEqual(reclaim_map["build-same-path"], 10 * gib)
        self.assertEqual(reclaim_map["drift-same-path"], 0)
        self.assertEqual(reclaim_map["git-pack-repo"], 0)
        self.assertEqual(reclaim_map["gov-doc-repo"], 0)
        self.assertEqual(reclaim_map["explicit-zero-item"], 0)
        self.assertEqual(reclaim_map["explicit-partial-item"], 2 * gib)

        # Total reclaimable across all 6 checked items must be exactly 12 GiB (10 + 2), NOT 34.5 GiB!
        self.assertEqual(res["summary"]["count"], 6)
        self.assertEqual(res["summary"]["bytes"], 12 * gib)
        self.assertEqual(res["poolMetrics"]["cleanedBytes"], 12 * gib)
        self.assertEqual(res["poolMetrics"]["retainedBytes"], 38 * gib)
        self.assertEqual(res["dockGib"], "12.00 GiB")

        # Non-cleanup cards still show friendly reference volume ("参考体量 ...")
        self.assertIn("参考体量 500 MiB", res["candidateListHtml"])
        self.assertIn("参考体量 10.00 GiB", res["candidateListHtml"])
        self.assertIn("参考体量 500 MiB", res["overviewListsHtml"])

    def test_dual_mode_uninstall_styles_fallback_desc_and_mixed_export(self) -> None:
        styles_text = (SCRIPTS_DIR / "dashboard_assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".uninstall-mode-box .um-head", styles_text)
        self.assertIn(".uninstall-mode-box .um-desc", styles_text)

        mib = 1024**2
        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "storage_gauges": [
                {
                    "id": "mac-data",
                    "label": "Mac Data",
                    "status": "ok",
                    "total_bytes": 100 * 1024**3,
                    "used_bytes": 50 * 1024**3,
                    "free_bytes": 50 * 1024**3,
                }
            ],
            "candidates": [
                {
                    "id": "build-1",
                    "domain": "mac",
                    "category": "build_artifact",
                    "tier": "A",
                    "title": "构建缓存产物",
                    "path": "/tmp/mock/target",
                    "size_bytes": 200 * mib,
                    "gauge_id": "mac-data",
                },
                {
                    "id": "app-alpha",
                    "domain": "mac",
                    "category": "app_uninstall",
                    "action_type": "cleanup",
                    "tier": "C",
                    "title": "AlphaStudio（180 天未打开）",
                    "path": "/Applications/AlphaStudio.app",
                    "selected_uninstall_mode": "keep_data",
                    "size_bytes": 500 * mib,
                    "gauge_id": "mac-data",
                    "uninstall_modes": {
                        "keep_data": {
                            "label": "保留用户数据",
                            "size_bytes": 300 * mib,
                            "paths": ["/Applications/AlphaStudio.app", "/tmp/Caches/AlphaStudio"],
                        },
                        "purge_all": {
                            "label": "一键全部删除",
                            "size_bytes": 500 * mib,
                            "paths": [
                                "/Applications/AlphaStudio.app",
                                "/tmp/Caches/AlphaStudio",
                                "/tmp/Support/AlphaStudio",
                            ],
                        },
                    },
                },
                {
                    "id": "app-beta",
                    "domain": "mac",
                    "category": "app_uninstall",
                    "action_type": "cleanup",
                    "tier": "C",
                    "title": "BetaPlayer（240 天未打开）",
                    "path": "/Applications/BetaPlayer.app",
                    "selected_uninstall_mode": "keep-data",
                    "size_bytes": 900 * mib,
                    "gauge_id": "mac-data",
                    "uninstall_modes": {
                        "keep-data": {
                            "label": "保留用户数据",
                            "size_bytes": 400 * mib,
                            "paths": ["/Applications/BetaPlayer.app"],
                        },
                        "purge-all": {
                            "label": "一键全部删除",
                            "size_bytes": 900 * mib,
                            "paths": ["/Applications/BetaPlayer.app", "/tmp/Support/BetaPlayer"],
                        },
                    },
                },
                {
                    "id": "git-pack-1",
                    "domain": "projects",
                    "category": "git_pack_bloat",
                    "tier": "B",
                    "title": "Git Pack 较大：repo-a",
                    "path": "/tmp/repo-a/.git/objects/pack",
                    "size_bytes": 150 * mib,
                    "gauge_id": "mac-data",
                },
            ],
        }
        res = self._run_in_node(snapshot, "dual_uninstall")
        self.assertIn(".app", res["descAlphaInit"])
        self.assertIn("保留", res["descAlphaInit"])
        self.assertIn(".app", res["descBetaInit"])
        self.assertIn("关联数据", res["descBetaAfter"])
        self.assertEqual(res["reclaimAlpha"], 300 * mib)
        self.assertEqual(res["reclaimBeta"], 900 * mib)

        prompt = res["prompt"]
        # Summary lines explicitly record each app's chosen mode
        self.assertIn("`app-alpha`: AlphaStudio", prompt)
        self.assertIn("`app-beta`: BetaPlayer", prompt)
        self.assertRegex(prompt, r"`app-alpha`[^\n]*--uninstall-mode keep-data")
        self.assertRegex(prompt, r"`app-beta`[^\n]*--uninstall-mode purge-all")

        # Separate preflight and quarantine commands per mode (never mixing alpha & beta in one flag!)
        self.assertIn('--ids "build-1"', prompt)
        self.assertIn('--ids "app-alpha" \\\n  --uninstall-mode keep-data', prompt)
        self.assertIn('--ids "app-beta" \\\n  --uninstall-mode purge-all', prompt)
        self.assertNotIn("app-alpha,app-beta", prompt)
        self.assertIn("preflight-keep-data.json", prompt)
        self.assertIn("preflight-purge-all.json", prompt)
        self.assertIn("quarantine-keep-data-plan.json", prompt)
        self.assertIn("quarantine-purge-all-plan.json", prompt)
        self.assertIn("`git-pack-1`", prompt)

    def test_payload_compaction_and_env_peripherals_panel(self) -> None:
        many_apps = [
            {
                "name": f"App{i}",
                "bundle_id": f"com.example.app{i}",
                "path": f"/Applications/App{i}.app",
                "total_bytes": 100_000_000,
                "cache_paths": [f"/tmp/cache/{i}/a", f"/tmp/cache/{i}/b"],
            }
            for i in range(120)
        ]
        many_pkgs = [{"name": f"pkg-{i}", "current": "1.0", "latest": "2.0", "raw_blob": "x" * 200} for i in range(40)]
        snapshot = {
            "generated_at": "2026-11-15T00:00:00+00:00",
            "config_path": "/private/secret/topology.local.json",
            "directory_sizes": {f"/tmp/dir/{i}": i * 1024 for i in range(300)},
            "storage_gauges": [],
            "app_inventory": {
                "total_apps": 120,
                "frequent_count": 100,
                "infrequent_count": 20,
                "total_bytes": 12_000_000_000,
                "apps": many_apps,
            },
            "environments": [
                {
                    "id": "brew",
                    "manager": "Homebrew Formulae",
                    "command": "brew upgrade --formula",
                    "packages": many_pkgs,
                    "unconsumed_raw_json": "y" * 5000,
                }
            ],
            "peripherals": {
                "storage_devices": [
                    {
                        "name": "Portable SSD",
                        "vendor": "Samsung",
                        "firmware_version": "1B6Q",
                        "smart_status": "Failing",
                        "link_speed": "10 Gb/s",
                        "transport": "USB",
                        "spusb_raw_tree": {"deep": "z" * 4000},
                    }
                ],
                "docks_and_hubs": [],
                "input_devices": [
                    {
                        "name": "Wireless Mouse",
                        "vendor": "Logitech",
                        "firmware_version": "22.1",
                        "battery_pct": 12,
                        "transport": "Bluetooth",
                    }
                ],
            },
            "candidates": [
                {
                    "id": "app-u1",
                    "category": "app_uninstall",
                    "title": "DemoApp",
                    "path": "/Applications/DemoApp.app",
                    "app_only_paths": ["/Applications/DemoApp.app"] * 10,
                    "all_uninstall_paths": ["/Applications/DemoApp.app"] * 20,
                    "size_bytes": 1024,
                }
            ],
        }
        payload = render_dashboard.build_dashboard_payload(snapshot)
        self.assertNotIn("config_path", payload)
        self.assertNotIn("directory_sizes", payload)
        self.assertNotIn("apps", payload["app_inventory"])
        self.assertEqual(payload["app_inventory"]["total_apps"], 120)
        self.assertNotIn("app_only_paths", payload["candidates"][0])
        self.assertNotIn("all_uninstall_paths", payload["candidates"][0])
        self.assertEqual(payload["environments"][0]["total_packages"], 40)
        self.assertEqual(len(payload["environments"][0]["packages"]), render_dashboard.MAX_ENV_PACKAGE_PREVIEW)
        self.assertNotIn("raw_blob", payload["environments"][0]["packages"][0])
        self.assertNotIn("spusb_raw_tree", payload["peripherals"]["storage_devices"][0])

        res = self._run_in_node(snapshot, "env_periph")
        self.assertFalse(res["hidden"])
        self.assertIn("40 个包", res["summary"])
        self.assertIn("2 台已连接存储/外设硬件", res["summary"])
        self.assertIn("Homebrew Formulae", res["contentHtml"])
        self.assertIn("SMART: Failing", res["contentHtml"])
        self.assertIn("电量 12%", res["contentHtml"])


if __name__ == "__main__":
    unittest.main()
