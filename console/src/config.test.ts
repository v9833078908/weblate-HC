import { beforeEach, describe, expect, it } from "vitest";

import { readConsoleConfig } from "./config";

describe("readConsoleConfig", () => {
  beforeEach(() => {
    document.head.innerHTML = `
      <meta name="console-base" content="/weblate/console/">
      <meta name="producer-console-root" content="/weblate/console/">
      <meta name="producer-api-root" content="/weblate/api/producer/">
      <meta name="producer-login-url" content="/weblate/accounts/login/">
      <meta name="csrf-token" content="csrf-test">
    `;
  });

  it("reads prefix-safe console configuration from the meta elements", () => {
    expect(readConsoleConfig(document)).toEqual({
      consoleRoot: "/weblate/console/",
      apiRoot: "/weblate/api/producer/",
      loginUrl: "/weblate/accounts/login/",
      csrfToken: "csrf-test",
    });
  });

  it("fails closed when any required meta value is absent", () => {
    document.querySelector('meta[name="csrf-token"]')?.remove();
    expect(() => readConsoleConfig(document)).toThrow("csrf-token");
  });

  it("fails closed when the compatibility console roots disagree", () => {
    document
      .querySelector('meta[name="producer-console-root"]')
      ?.setAttribute("content", "/other/console/");
    expect(() => readConsoleConfig(document)).toThrow("producer-console-root");
  });
});
