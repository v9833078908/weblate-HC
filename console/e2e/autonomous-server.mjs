import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {basename} from "node:path";

const root = new URL("../dist/", import.meta.url);
const meta = `<meta name="console-base" content="/console/"><meta name="producer-console-root" content="/console/"><meta name="producer-api-root" content="/api/producer/"><meta name="producer-login-url" content="/auth/login"><meta name="csrf-token" content="offline-test-only">`;
createServer(async (request,response)=>{
  if (request.method !== "GET" && request.method !== "HEAD") {response.writeHead(405); response.end(); return;}
  try {
    const path = new URL(request.url,"http://127.0.0.1").pathname;
    if (path.startsWith("/console/assets/")) {
      const file = basename(path);
      response.setHeader("Content-Type",file.endsWith(".css") ? "text/css" : "text/javascript");
      response.end(await readFile(new URL(`assets/${file}`,root))); return;
    }
    response.setHeader("Content-Type","text/html; charset=utf-8");
    response.end((await readFile(new URL("index.html",root),"utf8")).replace("<!--WIZARD_META-->",meta));
  } catch {response.writeHead(404); response.end();}
}).listen(18786,"127.0.0.1");
