(function () {
  "use strict";

  const html = document.documentElement;

  function effectiveTheme() {
    const set = html.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = effectiveTheme() === "dark" ? "light" : "dark";
      html.setAttribute("data-theme", next);
      localStorage.setItem("testprep-theme", next);
      document.dispatchEvent(new CustomEvent("testprep:theme", { detail: next }));
    });
  }

  function toast(message, kind) {
    let host = document.querySelector(".toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "toasts";
      document.body.appendChild(host);
    }
    const t = document.createElement("div");
    t.className = "toast toast-" + (kind || "info");
    t.setAttribute("role", "status");
    t.textContent = message;
    host.appendChild(t);
    setTimeout(() => t.classList.add("leaving"), 3200);
    setTimeout(() => t.remove(), 3600);
  }

  // Auto-dismiss server flash messages.
  document.querySelectorAll(".flash").forEach((f) => {
    setTimeout(() => f.classList.add("leaving"), 4500);
    setTimeout(() => f.remove(), 5000);
  });

  // Render text with `inline code` spans, without ever interpreting it as HTML.
  function rich(el, text) {
    el.textContent = "";
    String(text || "").split(/`([^`\n]+)`/).forEach((part, i) => {
      if (i % 2) {
        const code = document.createElement("code");
        code.textContent = part;
        el.appendChild(code);
      } else if (part) {
        el.appendChild(document.createTextNode(part));
      }
    });
  }

  // Question text: first line is prose; following lines are a code snippet, except a
  // trailing line ending in "?" which is the actual question.
  function questionText(el, text) {
    const lines = String(text || "").split("\n");
    if (lines.length < 2) return rich(el, text);
    const hasTail = lines.length >= 3 && lines[lines.length - 1].trim().endsWith("?");
    el.textContent = "";
    const lead = document.createElement("span");
    rich(lead, lines[0]);
    const pre = document.createElement("pre");
    pre.className = "snippet";
    pre.textContent = lines.slice(1, hasTail ? -1 : undefined).join("\n");
    el.append(lead, pre);
    if (hasTail) {
      const tail = document.createElement("span");
      rich(tail, lines[lines.length - 1]);
      el.appendChild(tail);
    }
  }

  window.TestPrep = { toast, effectiveTheme, rich, questionText };
})();
