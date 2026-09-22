/* Copyright © HCGameLoc
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

(() => {
  const getCookie = (name) =>
    document.cookie
      .split("; ")
      .find((row) => row.startsWith(`${name}=`))
      ?.split("=")[1];

  for (const button of document.querySelectorAll(".js-rename-key")) {
    button.addEventListener("click", async () => {
      const newKey = window.prompt(button.textContent || "Rename key");
      if (newKey === null) return;
      const request = async (data) => {
        const response = await fetch(button.dataset.url, {
          method: "POST",
          headers: {
            "X-CSRFToken": decodeURIComponent(getCookie("csrftoken") || ""),
          },
          body: new URLSearchParams(data),
        });
        return response.json();
      };
      const preview = await request({ stage: "preview", new_key: newKey });
      if (preview.error) return window.alert(preview.error);
      if (
        !window.confirm(
          `${preview.preview.old_context} → ${preview.preview.new_context}`,
        )
      )
        return;
      const result = await request({ token: preview.token });
      if (result.error) return window.alert(result.error);
      window.location.assign(result.url);
    });
  }

  for (const button of document.querySelectorAll(".js-move-string")) {
    button.addEventListener("click", async () => {
      const key = window.prompt("Move after key");
      if (!key) return;
      const results = await fetch(
        `${button.dataset.url}?q=${encodeURIComponent(key)}`,
      );
      const match = (await results.json()).results?.find(
        (item) => item.key === key,
      );
      if (!match) return window.alert("Anchor key was not found.");
      const request = async (data) => {
        const response = await fetch(button.dataset.url, {
          method: "POST",
          headers: {
            "X-CSRFToken": decodeURIComponent(getCookie("csrftoken") || ""),
          },
          body: new URLSearchParams(data),
        });
        return response.json();
      };
      const preview = await request({
        stage: "preview",
        anchor: match.id,
        placement: "after",
      });
      if (preview.error) return window.alert(preview.error);
      if (!window.confirm("Move this string after the selected key?")) return;
      const result = await request({ token: preview.token });
      if (result.error) return window.alert(result.error);
      window.location.reload();
    });
  }
})();
