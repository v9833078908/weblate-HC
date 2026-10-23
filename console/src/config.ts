export interface ConsoleConfig {
  consoleRoot: string;
  apiRoot: string;
  loginUrl: string;
  csrfToken: string;
}

function readMeta(document: Document, name: string): string {
  const value = document.querySelector<HTMLMetaElement>(
    `meta[name="${name}"]`,
  )?.content;
  if (!value) {
    throw new Error(`Missing required console meta value: ${name}`);
  }
  return value;
}

export function readConsoleConfig(document: Document): ConsoleConfig {
  const consoleRoot = readMeta(document, "console-base");
  const compatibilityRoot = readMeta(document, "producer-console-root");
  if (compatibilityRoot !== consoleRoot) {
    throw new Error(
      "producer-console-root must match the canonical console-base value",
    );
  }

  return {
    consoleRoot,
    apiRoot: readMeta(document, "producer-api-root"),
    loginUrl: readMeta(document, "producer-login-url"),
    csrfToken: readMeta(document, "csrf-token"),
  };
}
