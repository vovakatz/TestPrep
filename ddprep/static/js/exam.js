(function () {
  "use strict";

  const root = document.getElementById("exam");
  if (!root) return;

  const items = JSON.parse(document.getElementById("exam-data").textContent);
  const answerUrl = root.dataset.answerUrl;
  const startedAt = Date.parse(root.dataset.startedAt);
  const LETTERS = ["A", "B", "C", "D", "E"];

  const el = (id) => document.getElementById(id);
  const ui = {
    index: el("q-index"),
    number: el("q-number"),
    text: el("q-text"),
    difficulty: el("q-difficulty"),
    choices: el("choices"),
    feedback: el("feedback"),
    feedbackIcon: el("feedback-icon"),
    feedbackTitle: el("feedback-title"),
    feedbackTopic: el("feedback-topic"),
    feedbackText: el("feedback-text"),
    feedbackRef: el("feedback-ref"),
    prev: el("prev-btn"),
    skip: el("skip-btn"),
    submit: el("submit-btn"),
    next: el("next-btn"),
    grid: el("nav-grid"),
    progress: el("progress-fill"),
    liveCorrect: el("live-correct"),
    timer: el("timer"),
    finishBtn: el("finish-btn"),
    dialog: el("finish-dialog"),
    finishMsg: el("finish-message"),
    finishCancel: el("finish-cancel"),
    finishConfirm: el("finish-confirm"),
    finishForm: el("finish-form"),
  };

  const ICON_OK = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const ICON_BAD = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  let current = Math.max(0, items.findIndex((q) => !q.answered));
  if (items.every((q) => q.answered)) current = items.length - 1;
  const pending = {}; // index -> choice id selected but not yet submitted
  let busy = false;

  // --- navigator -----------------------------------------------------------------------------

  const navButtons = items.map((q, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "nav-cell";
    b.textContent = String(i + 1);
    b.setAttribute("aria-label", "Question " + (i + 1));
    b.addEventListener("click", () => go(i));
    ui.grid.appendChild(b);
    return b;
  });

  function refreshChrome() {
    const answered = items.filter((q) => q.answered).length;
    const correct = items.filter((q) => q.is_correct).length;
    ui.progress.style.width = (100 * answered) / items.length + "%";
    ui.liveCorrect.textContent = String(correct);
    navButtons.forEach((b, i) => {
      const q = items[i];
      b.classList.toggle("is-current", i === current);
      b.classList.toggle("is-correct", q.answered && q.is_correct);
      b.classList.toggle("is-wrong", q.answered && !q.is_correct);
      b.classList.toggle("is-pending", !q.answered && pending[i] !== undefined);
    });
  }

  // --- question rendering ------------------------------------------------------------------------

  function render() {
    const q = items[current];
    ui.index.textContent = String(current + 1);
    ui.number.textContent = String(current + 1);
    window.DDPrep.questionText(ui.text, q.question);
    ui.difficulty.textContent = "";
    ui.difficulty.title = ["", "Recall", "Applied", "Challenging"][q.difficulty] || "";
    for (let d = 1; d <= 3; d++) {
      const pip = document.createElement("span");
      pip.className = "pip" + (d <= q.difficulty ? " on" : "");
      ui.difficulty.appendChild(pip);
    }

    ui.choices.textContent = "";
    q.choices.forEach((c, ci) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "choice";
      b.setAttribute("role", "radio");
      b.dataset.choiceId = c.id;
      const letter = document.createElement("span");
      letter.className = "choice-letter";
      letter.textContent = LETTERS[ci];
      const text = document.createElement("span");
      text.className = "choice-text";
      window.DDPrep.rich(text, c.text);
      const mark = document.createElement("span");
      mark.className = "choice-mark";
      mark.setAttribute("aria-hidden", "true");
      b.append(letter, text, mark);

      const selected = q.answered ? q.selected === c.id : pending[current] === c.id;
      b.setAttribute("aria-checked", selected ? "true" : "false");
      if (selected) b.classList.add("is-selected");
      if (q.answered) {
        b.disabled = true;
        if (c.id === q.correct_choice) {
          b.classList.add("is-correct");
          mark.innerHTML = ICON_OK;
        } else if (c.id === q.selected) {
          b.classList.add("is-wrong");
          mark.innerHTML = ICON_BAD;
        } else {
          b.classList.add("is-dimmed");
        }
      } else {
        b.addEventListener("click", () => select(c.id));
      }
      ui.choices.appendChild(b);
    });

    if (q.answered) {
      ui.feedback.hidden = false;
      ui.feedback.className = "feedback " + (q.is_correct ? "feedback-ok" : "feedback-bad");
      ui.feedbackIcon.innerHTML = q.is_correct ? ICON_OK : ICON_BAD;
      ui.feedbackTitle.textContent = q.is_correct ? "Correct" : "Not quite";
      ui.feedbackTopic.textContent = q.domain + " · " + q.topic;
      window.DDPrep.rich(ui.feedbackText, q.explanation);
      if (q.reference) {
        ui.feedbackRef.hidden = false;
        ui.feedbackRef.href = q.reference;
      } else {
        ui.feedbackRef.hidden = true;
      }
    } else {
      ui.feedback.hidden = true;
    }

    ui.submit.hidden = q.answered;
    ui.skip.hidden = q.answered;
    ui.next.hidden = !q.answered;
    ui.submit.disabled = pending[current] === undefined;
    ui.prev.disabled = current === 0;
    ui.next.textContent = items.every((x) => x.answered) ? "See results →" : "Next question →";
    refreshChrome();
  }

  function select(choiceId) {
    if (items[current].answered) return;
    pending[current] = choiceId;
    ui.choices.querySelectorAll(".choice").forEach((b) => {
      const on = Number(b.dataset.choiceId) === choiceId;
      b.classList.toggle("is-selected", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    ui.submit.disabled = false;
    refreshChrome();
  }

  function go(i) {
    current = Math.min(items.length - 1, Math.max(0, i));
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function nextUnanswered(from) {
    for (let step = 1; step <= items.length; step++) {
      const i = (from + step) % items.length;
      if (!items[i].answered) return i;
    }
    return -1;
  }

  async function submit() {
    const q = items[current];
    const choiceId = pending[current];
    if (busy || q.answered || choiceId === undefined) return;
    busy = true;
    ui.submit.disabled = true;
    try {
      const res = await fetch(answerUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position: q.position, choice_id: choiceId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not save your answer.");
      Object.assign(q, {
        answered: true,
        selected: data.selected,
        is_correct: data.is_correct,
        correct_choice: data.correct_choice,
        explanation: data.explanation,
        reference: data.reference,
      });
      delete pending[current];
      render();
      ui.next.focus();
    } catch (err) {
      window.DDPrep.toast(err.message, "error");
      ui.submit.disabled = false;
    } finally {
      busy = false;
    }
  }

  function advance() {
    const i = nextUnanswered(current);
    if (i === -1) openFinish();
    else go(i);
  }

  // --- finishing -------------------------------------------------------------------------------

  function openFinish() {
    const open = items.filter((q) => !q.answered).length;
    ui.finishMsg.textContent =
      open === 0
        ? "You've answered every question. Ready to see your score?"
        : "You still have " + open + " unanswered question" + (open === 1 ? "" : "s") +
          ". Unanswered questions are scored as incorrect.";
    if (typeof ui.dialog.showModal === "function") ui.dialog.showModal();
    else if (confirm(ui.finishMsg.textContent)) ui.finishForm.submit();
  }

  ui.finishBtn.addEventListener("click", openFinish);
  ui.finishCancel.addEventListener("click", () => ui.dialog.close());
  ui.finishConfirm.addEventListener("click", () => ui.finishForm.submit());

  // --- controls ---------------------------------------------------------------------------------

  ui.submit.addEventListener("click", submit);
  ui.next.addEventListener("click", advance);
  ui.prev.addEventListener("click", () => go(current - 1));
  ui.skip.addEventListener("click", () => go((current + 1) % items.length));

  document.addEventListener("keydown", (e) => {
    if (ui.dialog.open || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    const q = items[current];
    const n = "1234abcd".indexOf(e.key.toLowerCase());
    if (n !== -1 && !q.answered) {
      const idx = n % 4;
      if (q.choices[idx]) select(q.choices[idx].id);
      e.preventDefault();
    } else if (e.key === "Enter") {
      if (q.answered) advance();
      else submit();
      e.preventDefault();
    } else if (e.key === "ArrowRight") {
      go(current + 1);
    } else if (e.key === "ArrowLeft") {
      go(current - 1);
    }
  });

  // --- timer ------------------------------------------------------------------------------------

  function tick() {
    const s = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, "0");
    ui.timer.textContent = h ? h + ":" + String(m).padStart(2, "0") + ":" + sec : m + ":" + sec;
  }
  tick();
  setInterval(tick, 1000);

  render();
})();
