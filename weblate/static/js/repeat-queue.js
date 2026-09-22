/* Copyright © HCGameLoc
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
document.addEventListener("DOMContentLoaded", () => {
  const toggles = [...document.querySelectorAll(".rq-toggle")];

  const setOpen = (button, open) => {
    const panel = document.getElementById(button.getAttribute("aria-controls"));
    button.setAttribute("aria-expanded", String(open));
    panel.hidden = !open;
  };

  toggles.forEach((button, index) => {
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      toggles.forEach((other) => {
        setOpen(other, other === button && open);
      });
    });
    button.addEventListener("keydown", (event) => {
      const keys = { ArrowDown: 1, ArrowUp: -1, j: 1, J: 1, k: -1, K: -1 };
      let destination = null;
      if (event.key === "Home") destination = 0;
      if (event.key === "End") destination = toggles.length - 1;
      if (event.key in keys) {
        destination =
          (index + keys[event.key] + toggles.length) % toggles.length;
      }
      if (destination !== null) {
        event.preventDefault();
        toggles[destination].focus();
      }
    });
  });

  document.querySelectorAll(".rq-filters input").forEach((input) => {
    input.addEventListener("change", () => input.form.requestSubmit());
  });

  document.querySelectorAll(".rq-decision").forEach((form) => {
    const preview = form.querySelector(".rq-preview");
    const hint = form.querySelector(".rq-hint");
    const custom = form.querySelector(".rq-custom");
    const updateDecision = () => {
      const checked = form.querySelector("input[type='radio']:checked");
      const customSelected = checked?.dataset.choice === "custom";
      custom.hidden = !customSelected;
      preview.setAttribute("aria-disabled", String(!checked));
      hint.textContent = checked
        ? checked.dataset.choice === "keep"
          ? "Translations are not changed; the occurrences become independent."
          : "Review the affected occurrences before writing."
        : "Choose an option to continue.";
    };
    form.addEventListener("change", updateDecision);
    form.addEventListener("submit", (event) => {
      if (preview.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        hint.textContent = "Choose an option first.";
      }
    });
  });
});
