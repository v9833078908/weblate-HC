/* Copyright © HCGameLoc
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

(() => {
  const modal = document.querySelector("#source-unit-structure-modal");
  if (!modal) return;

  const form = modal.querySelector("#source-unit-structure-form");
  const title = modal.querySelector("#source-unit-structure-title");
  const label = modal.querySelector("#source-unit-structure-input-label");
  const input = modal.querySelector("#source-unit-structure-input");
  const placement = modal.querySelector("#source-unit-structure-placement");
  const placementGroup = modal.querySelector(
    ".source-unit-structure-placement",
  );
  const warning = modal.querySelector(".source-unit-structure-warning");
  const results = modal.querySelector(".source-unit-structure-results");
  const status = modal.querySelector("#source-unit-structure-status");
  const preview = modal.querySelector(".source-unit-structure-preview");
  const previewButton = modal.querySelector(
    ".source-unit-structure-preview-button",
  );
  const confirmButton = modal.querySelector(
    ".source-unit-structure-confirm-button",
  );
  const translate = (message) => globalThis.django?.gettext(message) || message;
  let operation;
  let selectedAnchor;
  let token;
  let searchTimer;

  const request = async (url, data) => {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken":
          document.querySelector("#link-post input[name=csrfmiddlewaretoken]")
            ?.value || "",
      },
      body: new URLSearchParams(data),
    });
    if (!response.headers.get("Content-Type")?.includes("application/json")) {
      throw new Error(
        `${translate("The server rejected the request.")} (${response.status} ${response.statusText})`,
      );
    }
    const payload = await response.json();
    if (!response.ok || payload.error) {
      throw new Error(payload.error || response.statusText);
    }
    return payload;
  };

  const setStatus = (message, error = false) => {
    status.textContent = message;
    status.classList.toggle("text-danger", error);
  };

  const setBusy = (busy) => {
    previewButton.disabled = busy;
    confirmButton.disabled = busy;
    input.disabled = busy;
    placement.disabled = busy;
  };

  const clearPreview = () => {
    token = undefined;
    preview.hidden = true;
    preview.textContent = "";
    confirmButton.hidden = true;
    previewButton.hidden = false;
  };

  const showAnchorResults = (items) => {
    results.replaceChildren();
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "list-group-item list-group-item-action";
      button.textContent = `${item.key} — ${item.source}`;
      button.addEventListener("click", () => {
        selectedAnchor = item;
        input.value = item.key;
        results.hidden = true;
        setStatus(translate("Anchor selected."));
        clearPreview();
      });
      results.append(button);
    }
    results.hidden = !items.length;
  };

  const loadAnchors = async () => {
    if (operation?.kind !== "move" || !input.value.trim()) return;
    try {
      const response = await fetch(
        `${operation.url}?q=${encodeURIComponent(input.value.trim())}`,
      );
      const payload = await response.json();
      if (!response.ok || payload.error) {
        throw new Error(payload.error || response.statusText);
      }
      showAnchorResults(payload.results || []);
      if (!payload.results?.length) {
        setStatus(translate("No matching anchor key found."));
      }
    } catch (error) {
      setStatus(error.message, true);
    }
  };

  const open = (button, kind) => {
    operation = {
      kind,
      url: button.dataset.url,
      key: button.dataset.key || "",
    };
    selectedAnchor = undefined;
    clearPreview();
    results.hidden = true;
    setStatus("");
    warning.hidden = kind !== "rename";
    placementGroup.hidden = kind !== "move";
    title.textContent = translate(
      kind === "rename" ? "Rename key" : "Move string",
    );
    label.textContent = translate(kind === "rename" ? "New key" : "Anchor key");
    input.value = kind === "rename" ? operation.key : "";
    bootstrap.Modal.getOrCreateInstance(modal).show();
  };

  document.querySelectorAll(".js-rename-key").forEach((button) => {
    button.addEventListener("click", () => open(button, "rename"));
  });
  document.querySelectorAll(".js-move-string").forEach((button) => {
    button.addEventListener("click", () => open(button, "move"));
  });

  modal.addEventListener("shown.bs.modal", () => input.focus());
  input.addEventListener("input", () => {
    clearPreview();
    if (operation?.kind !== "move") return;
    selectedAnchor = undefined;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadAnchors, 200);
  });
  placement.addEventListener("change", clearPreview);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearPreview();
    const value = input.value.trim();
    if (!value) {
      setStatus(translate("This field is required."), true);
      input.focus();
      return;
    }
    if (operation.kind === "move" && !selectedAnchor) {
      setStatus(translate("Choose an anchor from the results."), true);
      return;
    }
    setBusy(true);
    try {
      const data =
        operation.kind === "rename"
          ? { stage: "preview", new_key: value }
          : {
              stage: "preview",
              anchor: selectedAnchor.id,
              placement: placement.value,
            };
      const payload = await request(operation.url, data);
      token = payload.token;
      if (operation.kind === "rename") {
        preview.textContent = translate(
          "Rename %(old)s to %(new)s in %(translations)s translations and %(files)s files.",
        )
          .replace("%(old)s", payload.preview.old_context)
          .replace("%(new)s", payload.preview.new_context)
          .replace(
            "%(translations)s",
            payload.preview.affected_translation_count,
          )
          .replace("%(files)s", payload.preview.affected_file_count);
      } else {
        const before = payload.preview.previous_context || translate("start");
        const after = payload.preview.next_context || translate("end");
        preview.textContent = translate(
          "Move from position %(old)s to %(new)s, between %(before)s and %(after)s.",
        )
          .replace("%(old)s", payload.preview.old_position)
          .replace("%(new)s", payload.preview.new_position)
          .replace("%(before)s", before)
          .replace("%(after)s", after);
      }
      preview.hidden = false;
      previewButton.hidden = true;
      const noChange =
        operation.kind === "rename"
          ? payload.preview.old_context === payload.preview.new_context
          : payload.preview.old_position === payload.preview.new_position;
      confirmButton.hidden = noChange;
      if (noChange) {
        setStatus(translate("This operation would not change the string."));
      } else {
        setStatus(translate("Review the change and confirm it."));
        confirmButton.focus();
      }
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  confirmButton.addEventListener("click", async () => {
    if (!token) return;
    setBusy(true);
    try {
      const payload = await request(operation.url, { token });
      window.location.assign(payload.url);
    } catch (error) {
      clearPreview();
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  });
})();
