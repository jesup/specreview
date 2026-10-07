const KIND_LABEL = {implements: "impl", partial: "part", structural: "struct", deviates: "DEV", unimplemented: "MISSING"};
const KIND_ORDER = ["deviates", "partial", "implements", "structural"];
const COLOR = {
  implements: "var(--implements)", partial: "var(--partial)", structural: "var(--structural)",
  deviates: "var(--deviates)", unimplemented: "var(--unimplemented)",
};
const LOW = 0.5;

let D, linksByStep, linksByLine, stepsById, docOf, stepEls, rowEls, hits = [], hitIdx = 0, curUrl = null;

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const lk = (file, side, n) => `${file}:${side === "new" ? "n" : "o"}${n}`;
const stepNum = (id) => id.slice(id.lastIndexOf(":") + 1);
const $ = (id) => document.getElementById(id);

async function load() {
  D = await (await fetch("/api/review")).json();
  index();
  render();
}

function index() {
  linksByStep = {};
  linksByLine = {};
  stepsById = {};
  docOf = {};
  D.analysis.docs.forEach((d) =>
    d.steps.forEach((s) => {
      stepsById[s.id] = s;
      docOf[s.id] = d;
    })
  );
  D.analysis.links.forEach((l, i) => {
    (linksByStep[l.step_id] ||= []).push(i);
    for (const r of l.ranges)
      for (let n = r.start; n <= r.end; n++) (linksByLine[lk(r.file, r.side, n)] ||= []).push(i);
  });
}

function render() {
  stepEls = {};
  rowEls = {};
  hits = [];
  $("title").textContent = D.patch.id;
  const s = D.summary;
  const counts = Object.entries(s.steps).map(([k, v]) => `${v} ${k}`).join(", ");
  $("summary").textContent = `${counts}; ${s.unexplained_lines}/${s.added_lines} added lines unexplained`;
  renderLegend();
  renderSpecs();
  renderNotes();
  renderSpecPane();
  renderDiffPane();
  applyFilters();
  collapseDetail();
  curUrl = null;
  if (D.analysis.docs[0]) navigate(D.analysis.docs[0], null);
  else setRendered(null, "", "");
}

function renderLegend() {
  const lg = $("legend");
  lg.replaceChildren();
  for (const k of Object.keys(COLOR)) {
    const sp = el("span", "", KIND_LABEL[k]);
    sp.style.setProperty("--c", COLOR[k]);
    lg.append(sp);
  }
  const g = el("span", "hatch", "not in spec");
  g.style.setProperty("--c", "var(--notinspec)");
  const u = el("span", "hatch", "unexplained");
  u.style.setProperty("--c", "var(--unimplemented)");
  lg.append(g, u);
}

let specRefs = [];
function renderSpecs() {
  specRefs = D.analysis.docs.map((d) => d.ref);
  const box = $("spec-chips");
  box.replaceChildren();
  specRefs.forEach((ref) => {
    const c = el("span", "chip", ref + " ");
    const x = el("button", "", "x");
    x.title = "remove";
    x.onclick = () => {
      specRefs = specRefs.filter((r) => r !== ref);
      c.remove();
    };
    c.append(x);
    box.append(c);
  });
}

function renderNotes() {
  const items = [
    ...D.analysis.warnings.map((w) => `warning: ${w}`),
    ...D.analysis.findings.map((f) => `${f.severity}: ${f.text}`),
  ];
  const box = $("notes");
  box.hidden = items.length === 0;
  box.replaceChildren(...items.map((t) => el("div", "", t)));
}

function docAnchor(d) {
  return d.anchor || d.ref.slice(d.ref.indexOf("#") + 1);
}

function renderSpecPane() {
  const pane = $("outline");
  const mm = $("spec-mm");
  pane.replaceChildren();
  mm.replaceChildren();
  for (const d of D.analysis.docs) {
    const head = el("div", "doc-head", `${d.title}  (${d.ref})`);
    const open = el("a", "", "open in new tab");
    open.href = d.url;
    open.target = "_blank";
    open.rel = "noopener";
    head.append(open);
    pane.append(head);
    for (const s of d.steps) {
      const st = D.step_status[s.id];
      const row = el("div", `step st-${st}`);
      row.dataset.id = s.id;
      row.title = s.text;
      row.style.paddingLeft = `${4 + 16 * s.depth}px`;
      row.append(el("span", "num", s.number), el("span", "text", s.title || s.text), el("span", "badge", KIND_LABEL[st]));
      row.onclick = () => selectStep(s.id);
      pane.append(row);
      stepEls[s.id] = row;
      const tick = el("div");
      tick.style.background = COLOR[st];
      tick.onclick = () => row.scrollIntoView({block: "center"});
      mm.append(tick);
    }
  }
}

function setRendered(doc, target, url) {
  $("r-title").textContent = doc ? doc.title : "";
  $("r-target").textContent = target;
  const open = $("r-open");
  open.hidden = !url;
  if (url) open.href = url;
  if (url !== curUrl) {
    curUrl = url;
    if (url) $("r-frame").src = url;
    else $("r-frame").removeAttribute("src");
  }
}

function navigate(doc, step) {
  const frag = (step && step.target) || docAnchor(doc);
  const url = `${doc.url.split("#")[0]}#${frag}`;
  setRendered(doc, step ? `${step.number} ${step.title || ""}`.trim() : frag, url);
}

function kindsFor(key) {
  return D.line_status[key] || [];
}

function stripeFor(kinds) {
  const st = el("span", "stripe");
  const links = KIND_ORDER.filter((k) => kinds.includes(k));
  if (links.length) {
    const seg = 100 / links.length;
    st.style.background = `linear-gradient(to bottom, ${links
      .map((k, i) => `${COLOR[k]} ${i * seg}% ${(i + 1) * seg}%`)
      .join(",")})`;
  } else if (kinds.includes("not-in-spec:unexplained")) {
    st.classList.add("u");
  } else if (kinds.length) {
    st.classList.add("n");
  }
  return st;
}

function renderDiffPane() {
  const pane = $("diff");
  const mm = $("diff-mm");
  pane.replaceChildren();
  mm.replaceChildren();
  let order = 0;
  for (const f of D.patch.files) {
    pane.append(el("div", "file-head", f.path));
    for (const h of f.hunks) {
      pane.append(el("div", "hunk-head", h.header));
      for (const ln of h.lines) {
        const side = ln.kind === "del" ? "old" : "new";
        const n = side === "old" ? ln.old : ln.new;
        const key = lk(f.path, side, n);
        const kinds = ln.kind === "ctx" ? [] : kindsFor(key);
        const row = el("div", `line ${ln.kind}`);
        row.dataset.key = key;
        row.dataset.order = order++;
        const nums = [...new Set((linksByLine[key] || []).map((i) => stepNum(D.analysis.links[i].step_id)))];
        row.append(
          stripeFor(kinds),
          el("span", "ln", n),
          el("span", "code", (ln.kind === "add" ? "+" : ln.kind === "del" ? "-" : " ") + ln.text),
          el("span", "labels", nums.join(","))
        );
        row.onclick = () => selectLine(key);
        applyVerdict(row, key);
        pane.append(row);
        rowEls[key] = row;
        if (ln.kind !== "ctx") {
          const tick = el("div");
          const top = KIND_ORDER.find((k) => kinds.includes(k));
          tick.style.background = top ? COLOR[top] : kinds.includes("not-in-spec:unexplained") ? COLOR.unimplemented : "#bbb";
          tick.onclick = () => row.scrollIntoView({block: "center"});
          mm.append(tick);
        }
      }
    }
  }
}

function applyVerdict(row, key) {
  const idxs = linksByLine[key] || [];
  const allWrong = idxs.length > 0 && idxs.every((i) => D.overrides[D.link_keys[i]] === "wrong");
  row.classList.toggle("wrong", allWrong);
}

function clearSel() {
  document.querySelectorAll(".sel, .hit").forEach((e) => e.classList.remove("sel", "hit"));
}

function largestRun(id) {
  const groups = {};
  for (const i of linksByStep[id] || [])
    for (const r of D.analysis.links[i].ranges) (groups[`${r.file}\0${r.side}`] ||= {file: r.file, side: r.side, spans: []}).spans.push([r.start, r.end]);
  let best = null;
  for (const g of Object.values(groups)) {
    g.spans.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [a, b] of g.spans) {
      const last = merged[merged.length - 1];
      if (last && a <= last[1] + 1) last[1] = Math.max(last[1], b);
      else merged.push([a, b]);
    }
    for (const [a, b] of merged) {
      const rows = [];
      for (let n = a; n <= b; n++) {
        const row = rowEls[lk(g.file, g.side, n)];
        if (row) rows.push(row);
      }
      if (!rows.length) continue;
      const first = Math.min(...rows.map((r) => +r.dataset.order));
      if (!best || rows.length > best.len || (rows.length === best.len && first < best.first)) best = {len: rows.length, first};
    }
  }
  return best;
}

function selectStep(id) {
  clearSel();
  stepEls[id].classList.add("sel");
  hits = [];
  for (const i of linksByStep[id] || [])
    for (const r of D.analysis.links[i].ranges)
      for (let n = r.start; n <= r.end; n++) {
        const row = rowEls[lk(r.file, r.side, n)];
        if (row && !hits.includes(row)) hits.push(row);
      }
  hits.sort((a, b) => a.dataset.order - b.dataset.order);
  hits.forEach((r) => r.classList.add("hit"));
  hitIdx = 0;
  const run = largestRun(id);
  if (run) hitIdx = hits.findIndex((r) => +r.dataset.order === run.first);
  navigate(docOf[id], stepsById[id]);
  showStepDetail(id);
  if (run) hits[hitIdx].scrollIntoView({block: "center"});
}

function selectLine(key) {
  clearSel();
  rowEls[key].classList.add("hit");
  const idxs = linksByLine[key] || [];
  idxs.forEach((i) => stepEls[D.analysis.links[i].step_id]?.classList.add("sel"));
  const firstId = idxs.length ? D.analysis.links[idxs[0]].step_id : "";
  if (firstId) {
    stepEls[firstId].scrollIntoView({block: "center"});
    navigate(docOf[firstId], stepsById[firstId]);
  }
  showLineDetail(key, idxs);
}

function jump(delta) {
  if (!hits.length) return;
  hitIdx = (hitIdx + delta + hits.length) % hits.length;
  hits[hitIdx].scrollIntoView({block: "center"});
}

function linkItem(i, withVerdict) {
  const l = D.analysis.links[i];
  const key = D.link_keys[i];
  const box = el("div", "item");
  const k = el("span", "k", `${KIND_LABEL[l.kind]} ${Math.round(l.confidence * 100)}%`);
  k.style.setProperty("--c", COLOR[l.kind]);
  const step = stepsById[l.step_id];
  box.append(k, el("span", "", ` ${step ? step.number + " " + step.text : l.step_id}`), el("div", "", l.rationale));
  if (withVerdict) {
    const row = el("div");
    for (const v of ["ok", "wrong"]) {
      const b = el("button", D.overrides[key] === v ? "on" : "", v);
      b.onclick = async (e) => {
        e.stopPropagation();
        const next = D.overrides[key] === v ? null : v;
        await fetch(`/api/overrides/${key}`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({verdict: next}),
        });
        if (next) D.overrides[key] = next;
        else delete D.overrides[key];
        for (const r of Object.values(rowEls)) applyVerdict(r, r.dataset.key);
        for (const b of row.children) b.classList.toggle("on", D.overrides[key] === b.textContent);
      };
      row.append(b);
    }
    box.append(row);
  }
  return box;
}

function setDetail(title) {
  $("detail-title").textContent = title;
  $("detail").classList.remove("collapsed");
  $("detail-toggle").textContent = "hide";
}

function collapseDetail() {
  $("detail").classList.add("collapsed");
  $("detail-toggle").textContent = "show";
}

function showStepDetail(id) {
  const s = stepsById[id];
  const body = $("detail-body");
  body.replaceChildren(el("div", "", `${s.number} ${s.text}`));
  const idxs = linksByStep[id] || [];
  if (!idxs.length) body.append(el("div", "item", `No code maps to this step (${D.step_status[id]}).`));
  idxs.forEach((i) => body.append(linkItem(i, true)));
  if (hits.length) body.append(el("div", "item", `${hits.length} lines; n / p to step through`));
  setDetail(`Step ${s.number}: ${s.title || s.text}`);
}

function showLineDetail(key, idxs) {
  const body = $("detail-body");
  body.replaceChildren();
  idxs.forEach((i) => body.append(linkItem(i, true)));
  for (const u of D.analysis.unmapped)
    for (const r of u.ranges)
      if (inRange(key, r)) {
        body.append(el("div", "item", `not in spec (${u.reason}): ${u.note}`));
      }
  if (!body.childNodes.length) body.append(el("div", "", (kindsFor(key)[0] || "context line")));
  setDetail(`Line ${key.slice(key.lastIndexOf(":") + 2)}`);
}

function inRange(key, r) {
  const m = key.match(/^(.*):([no])(\d+)$/);
  return m && m[1] === r.file && (m[2] === "n") === (r.side === "new") && +m[3] >= r.start && +m[3] <= r.end;
}

function applyFilters() {
  const f = {
    steps: $("f-steps").checked, code: $("f-code").checked,
    low: $("f-low").checked, dev: $("f-dev").checked,
  };
  const any = Object.values(f).some(Boolean);
  for (const [id, e] of Object.entries(stepEls)) {
    const st = D.step_status[id];
    const lows = (linksByStep[id] || []).some((i) => D.analysis.links[i].confidence < LOW);
    const match = (f.steps && st === "unimplemented") || (f.dev && st === "deviates") || (f.low && lows);
    e.classList.toggle("dim", any && !match);
  }
  for (const [key, e] of Object.entries(rowEls)) {
    if (e.classList.contains("ctx")) {
      e.classList.toggle("dim", any);
      continue;
    }
    const kinds = kindsFor(key);
    const idxs = linksByLine[key] || [];
    const match =
      (f.code && kinds.includes("not-in-spec:unexplained")) ||
      (f.dev && kinds.includes("deviates")) ||
      (f.low && idxs.some((i) => D.analysis.links[i].confidence < LOW));
    e.classList.toggle("dim", any && !match);
  }
}

document.querySelectorAll("#bar input[type=checkbox]").forEach((c) => (c.onchange = applyFilters));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "n") jump(1);
  else if (e.key === "p") jump(-1);
  else if (e.key === "Escape") collapseDetail();
});

$("detail-toggle").onclick = () => {
  if ($("detail").classList.contains("collapsed")) setDetail($("detail-title").textContent);
  else collapseDetail();
};

document.querySelectorAll("#modes button").forEach((b) => {
  b.onclick = () => {
    $("spec-pane").className = `mode-${b.dataset.mode}`;
    document.querySelectorAll("#modes button").forEach((o) => o.classList.toggle("on", o === b));
  };
});

$("detail-grip").onmousedown = (e) => {
  e.preventDefault();
  const body = $("detail-body");
  const y0 = e.clientY;
  const h0 = body.offsetHeight;
  const move = (m) => (body.style.height = `${Math.max(40, h0 + y0 - m.clientY)}px`);
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
};

$("rerun").onclick = async () => {
  const extra = $("add-spec").value.trim();
  const refs = extra ? [...specRefs, extra] : specRefs;
  if (!refs.length) return;
  const btn = $("rerun");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  try {
    const r = await fetch("/api/rerun", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({specs: refs}),
    });
    if (!r.ok) throw new Error(await r.text());
    D = await r.json();
    $("add-spec").value = "";
    index();
    render();
  } catch (err) {
    alert(`re-run failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Re-run";
  }
};

load();
