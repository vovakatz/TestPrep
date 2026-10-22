(function () {
  "use strict";

  const host = document.getElementById("trend-chart");
  const dataEl = document.getElementById("trend-data");
  if (!host || !dataEl) return;

  const data = JSON.parse(dataEl.textContent);
  const NS = "http://www.w3.org/2000/svg";
  const HEIGHT = 240;
  const M = { top: 16, right: 48, bottom: 32, left: 44 };

  const toggle = document.getElementById("trend-table-toggle");
  const tableWrap = document.getElementById("trend-table-wrap");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const show = tableWrap.hidden;
      tableWrap.hidden = !show;
      toggle.setAttribute("aria-expanded", String(show));
      toggle.textContent = show ? "Hide table" : "Show table";
    });
  }

  function svgEl(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.hidden = true;

  function draw() {
    host.textContent = "";
    host.appendChild(tooltip);
    const width = Math.max(320, host.clientWidth);
    const w = width - M.left - M.right;
    const h = HEIGHT - M.top - M.bottom;
    const n = data.length;
    const x = (i) => M.left + (n === 1 ? w / 2 : (i * w) / (n - 1));
    const y = (v) => M.top + h - (v / 100) * h;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${width} ${HEIGHT}`,
      width: width,
      height: HEIGHT,
      class: "trend-svg",
      role: "img",
      "aria-label": "Line chart of exam scores. Latest " + Math.round(data[n - 1].score) + "%.",
      tabindex: "0",
    });
    host.appendChild(svg);

    // Gridlines and y ticks
    [0, 25, 50, 75, 100].forEach((v) => {
      svgEl("line", { x1: M.left, x2: M.left + w, y1: y(v), y2: y(v), class: v === 0 ? "axis-base" : "grid" }, svg);
      const t = svgEl("text", { x: M.left - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" }, svg);
      t.textContent = v + "%";
    });

    // X ticks: thin out so labels never collide (~1 per 56px)
    const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(w / 56))));
    data.forEach((d, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      const t = svgEl("text", { x: x(i), y: M.top + h + 20, class: "tick", "text-anchor": "middle" }, svg);
      t.textContent = "#" + (i + 1);
    });

    // Area wash + line
    if (n > 1) {
      const pts = data.map((d, i) => `${x(i)},${y(d.score)}`).join(" L");
      svgEl("path", { d: `M${x(0)},${y(0)} L${pts} L${x(n - 1)},${y(0)} Z`, class: "area" }, svg);
      svgEl("path", { d: `M${pts}`, class: "line" }, svg);
    }

    // Crosshair (drawn under the markers)
    const cross = svgEl("line", { y1: M.top, y2: M.top + h, class: "crosshair", visibility: "hidden" }, svg);

    // Markers with surface ring; targeted exams are hollow so mode doesn't rely on color
    const dots = data.map((d, i) =>
      svgEl("circle", { cx: x(i), cy: y(d.score), r: 4.5, class: "marker" + (d.mode === "targeted" ? " marker-hollow" : "") }, svg)
    );

    // Direct label on the latest point only
    const last = data[n - 1];
    const lbl = svgEl("text", { x: x(n - 1) + 10, y: y(last.score) + 4, class: "end-label" }, svg);
    lbl.textContent = Math.round(last.score) + "%";

    // Hover layer
    const hit = svgEl("rect", { x: M.left - 12, y: M.top, width: w + 24, height: h, class: "hit" }, svg);
    let active = -1;

    function show(i) {
      active = i;
      const d = data[i];
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      dots.forEach((c, j) => c.classList.toggle("is-active", j === i));
      tooltip.textContent = "";
      const v = document.createElement("strong");
      v.textContent = Math.round(d.score) + "%";
      const meta = document.createElement("span");
      meta.textContent = "Exam #" + (i + 1) + " · " + d.correct + "/" + d.total + " · " + (d.mode === "targeted" ? "Targeted" : "Random");
      const date = document.createElement("span");
      date.className = "muted";
      date.textContent = d.date;
      tooltip.append(v, meta, date);
      tooltip.hidden = false;
      const tw = tooltip.offsetWidth;
      let left = x(i) + 12;
      if (left + tw > width) left = x(i) - tw - 12;
      tooltip.style.left = left + "px";
      tooltip.style.top = Math.max(0, y(d.score) - 30) + "px";
    }

    function hide() {
      active = -1;
      cross.setAttribute("visibility", "hidden");
      dots.forEach((c) => c.classList.remove("is-active"));
      tooltip.hidden = true;
    }

    function nearest(evt) {
      const r = svg.getBoundingClientRect();
      const px = ((evt.clientX - r.left) / r.width) * width;
      if (n === 1) return 0;
      return Math.min(n - 1, Math.max(0, Math.round(((px - M.left) / w) * (n - 1))));
    }

    hit.addEventListener("pointermove", (e) => show(nearest(e)));
    hit.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(n - 1));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { show(Math.max(0, (active === -1 ? n : active) - 1)); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(Math.min(n - 1, active + 1)); e.preventDefault(); }
    });
  }

  draw();
  let raf = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(draw);
  });
  document.addEventListener("testprep:theme", draw);
})();
