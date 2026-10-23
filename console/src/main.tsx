import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ProducerApi } from "./api/client";
import { readConsoleConfig } from "./config";
import { createConsoleRouter } from "./router";
import "./styles.css";

const root = document.getElementById("producer-console-app");
if (!root) {
  throw new Error("Missing Producer Console application root.");
}

const config = readConsoleConfig(document);
const api = new ProducerApi(config);
const router = createConsoleRouter(api, config.consoleRoot);

createRoot(root).render(
  <StrictMode>
    <App router={router} />
  </StrictMode>,
);
