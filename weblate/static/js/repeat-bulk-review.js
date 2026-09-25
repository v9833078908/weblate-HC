/* Copyright © HCGameLoc
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".rq-bulk");
  const summary = document.getElementById("bulk-summary");
  if (!summary) return;
  const boxes = [...form.querySelectorAll("input[name='result']")];
  const submit = form.querySelector("button[type='submit']");

  const updateSummary = () => {
    const selected = boxes.filter((box) => box.checked);
    const places = selected.reduce(
      (total, box) => total + Number(box.closest("tr").dataset.writable),
      0,
    );
    summary.textContent = `${interpolate(
      ngettext("%s group selected,", "%s groups selected,", selected.length),
      [selected.length],
    )} ${interpolate(
      ngettext("%s place will change.", "%s places will change.", places),
      [places],
    )}`;
    submit.disabled = selected.length === 0;
  };
  form.addEventListener("change", updateSummary);
  updateSummary();

  const select = form.querySelector(".rq-bulk-select");
  select.hidden = false;
  select.addEventListener("click", (event) => {
    const mode = event.target.dataset.select;
    if (!mode) return;
    for (const box of boxes) {
      if (mode === "none") {
        box.checked = false;
      } else if (!box.closest(".rq-attention")) {
        // "Select all" never reaches the collapsed attention groups.
        box.checked = true;
      }
    }
    updateSummary();
  });

  // Pages only hide rows: every checkbox stays in the one form, so the
  // apply button submits the selection of every page.
  const table = form.querySelector("[data-page-size]");
  const pager = form.querySelector(".rq-bulk-pager");
  if (table && pager) {
    const rows = [...table.tBodies[0].rows];
    const size = Number(table.dataset.pageSize);
    const pages = Math.ceil(rows.length / size);
    const status = pager.querySelector("span");
    const [previous, next] = pager.querySelectorAll("button");
    let page = 0;
    const show = (index) => {
      page = Math.min(Math.max(index, 0), pages - 1);
      rows.forEach((row, position) => {
        row.hidden = Math.floor(position / size) !== page;
      });
      status.textContent = interpolate(gettext("Page %s of %s"), [
        page + 1,
        pages,
      ]);
      previous.disabled = page === 0;
      next.disabled = page === pages - 1;
    };
    if (pages > 1) {
      pager.hidden = false;
      pager.addEventListener("click", (event) => {
        const step = Number(event.target.dataset.page);
        if (!step) return;
        show(page + step);
        table.scrollIntoView({ block: "start" });
      });
      show(0);
    }
  }

  // Per-place detail is loaded only when a row is opened, and shown in a row
  // of its own under the group so it gets the full table width.
  for (const details of form.querySelectorAll(".rq-places")) {
    details.addEventListener("toggle", async () => {
      const row = details.closest("tr");
      let detail = row.nextElementSibling;
      if (!detail?.classList.contains("rq-places-row")) {
        if (!details.open) return;
        detail = document.createElement("tr");
        detail.className = "rq-places-row";
        detail.insertCell();
        const cell = detail.insertCell();
        cell.colSpan = row.cells.length - 1;
        // The body keeps the id the summary's aria-controls points to.
        cell.append(details.querySelector(".rq-places-body"));
        row.after(detail);
      }
      detail.hidden = !details.open;
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "1";
      const body = detail.querySelector(".rq-places-body");
      const fallback = body.querySelector("a");
      body.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(details.dataset.url, {
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error(response.statusText);
        const fetched = new DOMParser().parseFromString(
          await response.text(),
          "text/html",
        );
        const detail = fetched.querySelector(".rq-places-detail");
        if (!detail) throw new Error("missing detail");
        body.replaceChildren(document.importNode(detail, true));
      } catch {
        delete details.dataset.loaded;
        body.replaceChildren(
          `${gettext("The places could not be loaded.")} `,
          fallback,
        );
      } finally {
        body.removeAttribute("aria-busy");
      }
    });
  }
});
