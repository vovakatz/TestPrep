(function () {
  "use strict";

  const root = document.getElementById("study");
  if (!root) return;

  const cards = JSON.parse(document.getElementById("cards-data").textContent);
  const reviewUrl = root.dataset.reviewUrl;
  const el = (id) => document.getElementById(id);
  const ui = {
    flip: el("flip"),
    frontTopic: el("front-topic"),
    frontText: el("front-text"),
    backTopic: el("back-topic"),
    backText: el("back-text"),
    backRef: el("back-ref"),
    pos: el("card-pos"),
    fill: el("study-fill"),
    learning: el("rate-learning"),
    known: el("rate-known"),
    prev: el("prev-card"),
    next: el("next-card"),
    stage: el("stage"),
    done: el("done"),
    doneKnown: el("done-known"),
    doneLearning: el("done-learning"),
    retryForm: el("retry-form"),
    restart: el("restart-view"),
  };

  let index = cards.findIndex((c) => !c.rating);
  if (index === -1) index = 0;
  let flipped = false;
  let busy = false; // a rating is being saved; ignore input until it lands
  const allRated = () => cards.every((c) => c.rating);

  function render() {
    const c = cards[index];
    flipped = false;
    ui.flip.classList.remove("is-flipped");
    ui.frontTopic.textContent = c.topic;
    ui.backTopic.textContent = c.topic;
    window.TestPrep.rich(ui.frontText, c.front);
    window.TestPrep.rich(ui.backText, c.back);
    ui.backRef.hidden = !c.reference;
    if (c.reference) ui.backRef.href = c.reference;
    ui.pos.textContent = String(index + 1);
    ui.prev.disabled = index === 0;
    ui.next.disabled = index === cards.length - 1;
    ui.flip.dataset.rating = c.rating || "";
    updateRateButtons();
    const rated = cards.filter((x) => x.rating).length;
    ui.fill.style.width = (100 * rated) / cards.length + "%";
  }

  function updateRateButtons() {
    const c = cards[index];
    ui.learning.disabled = !flipped;
    ui.known.disabled = !flipped;
    ui.learning.classList.toggle("is-chosen", c.rating === "learning");
    ui.known.classList.toggle("is-chosen", c.rating === "known");
  }

  function flip() {
    if (busy) return;
    flipped = !flipped;
    ui.flip.classList.toggle("is-flipped", flipped);
    updateRateButtons();
  }

  async function rate(rating) {
    if (!flipped || busy) return;
    const c = cards[index];
    busy = true;
    try {
      const res = await fetch(reviewUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card_id: c.id, rating: rating }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "Could not save rating.");
      c.rating = rating;
    } catch (err) {
      window.TestPrep.toast(err.message, "error");
      return;
    } finally {
      busy = false;
    }
    const nextOpen = cards.findIndex((x, i) => i > index && !x.rating);
    const anyOpen = cards.findIndex((x) => !x.rating);
    if (nextOpen !== -1) index = nextOpen;
    else if (anyOpen !== -1) index = anyOpen;
    else return finish();
    render();
  }

  function finish() {
    const known = cards.filter((c) => c.rating === "known").length;
    const learning = cards.filter((c) => c.rating === "learning").length;
    ui.doneKnown.textContent = String(known);
    ui.doneLearning.textContent = String(learning);
    ui.retryForm.hidden = learning === 0;
    ui.stage.hidden = true;
    ui.done.hidden = false;
    ui.fill.style.width = "100%";
  }

  ui.flip.addEventListener("click", flip);
  ui.learning.addEventListener("click", () => rate("learning"));
  ui.known.addEventListener("click", () => rate("known"));
  ui.prev.addEventListener("click", () => { if (!busy && index > 0) { index--; render(); } });
  ui.next.addEventListener("click", () => { if (!busy && index < cards.length - 1) { index++; render(); } });
  ui.restart.addEventListener("click", () => {
    ui.done.hidden = true;
    ui.stage.hidden = false;
    index = 0;
    render();
  });

  document.addEventListener("keydown", (e) => {
    if (ui.stage.hidden || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === " " || e.key === "Enter") {
      flip();
      e.preventDefault();
    } else if (e.key === "1") {
      rate("learning");
    } else if (e.key === "2") {
      rate("known");
    } else if (e.key === "ArrowLeft") {
      ui.prev.click();
    } else if (e.key === "ArrowRight") {
      ui.next.click();
    }
  });

  if (!cards.length) return;
  if (allRated()) finish();
  else render();
})();
