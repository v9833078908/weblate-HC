// Copyright © Michal Čihař <michal@weblate.org>
//
// SPDX-License-Identifier: GPL-3.0-or-later

const clarityTracker = document.getElementById("clarity-tracker");

if (typeof window.clarity !== "function") {
  const queue = [];
  const clarity = (...args) => {
    queue.push(args);
  };
  clarity.q = queue;
  window.clarity = clarity;
}

const clarityScript = document.createElement("script");
clarityScript.async = true;
clarityScript.src = `https://www.clarity.ms/tag/${clarityTracker.dataset.projectId}`;
document.head.append(clarityScript);

window.clarity("consentv2", {
  ad_Storage: "denied",
  analytics_Storage: "granted",
});
window.clarity("set", "language", clarityTracker.dataset.language);
if (clarityTracker.dataset.project !== undefined) {
  window.clarity("set", "project", clarityTracker.dataset.project);
}
if (clarityTracker.dataset.username !== undefined) {
  window.clarity("identify", clarityTracker.dataset.username);
}
