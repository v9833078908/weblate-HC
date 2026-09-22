/* Copyright © HCGameLoc
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
document.addEventListener("click", (event) => {
  const button = event.target.closest(".rq-toggle");
  if (!button) return;
  const panel = document.getElementById(button.getAttribute("aria-controls"));
  const open = button.getAttribute("aria-expanded") === "true";
  document
    .querySelectorAll(".rq-toggle[aria-expanded='true']")
    .forEach((other) => {
      other.setAttribute("aria-expanded", "false");
      document.getElementById(other.getAttribute("aria-controls")).hidden =
        true;
    });
  button.setAttribute("aria-expanded", String(!open));
  panel.hidden = open;
});
