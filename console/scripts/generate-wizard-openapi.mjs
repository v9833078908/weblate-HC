import {spawnSync} from "node:child_process";
import {readFileSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {parseDocument, stringify} from "yaml";

const root = fileURLToPath(new URL("../..", import.meta.url));
const python = process.env.WIZARD_SCHEMA_PYTHON ?? resolve(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const source = `import json\nfrom pathlib import Path\nfrom tempfile import TemporaryDirectory\nfrom app.main import create_app\nfrom app.settings import Settings\nwith TemporaryDirectory() as directory:\n app = create_app(Settings(data_dir=Path(directory)))\n result = app.openapi()\n app.state.store.engine.dispose()\n print(json.dumps(result))\n`;
const result = spawnSync(python, ["-c", source], {cwd:root, encoding:"utf8", env:{...process.env, PYTHONPATH:resolve(root,"backend")}});
if (result.status !== 0) throw new Error(result.stderr || "Schema generation failed");
const wizard = JSON.parse(result.stdout);
const names = new Set(Object.keys(wizard.components.schemas));
function remap(value) {
  if (Array.isArray(value)) return value.map(remap);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    key === "$ref" && typeof item === "string" && item.startsWith("#/components/schemas/") && names.has(item.split("/").at(-1))
      ? item.replace("#/components/schemas/", "#/components/schemas/Wizard") : remap(item)]));
}
const path = resolve(root, "docs/specs/openapi.yaml");
let original = readFileSync(path, "utf8");
const document = parseDocument(original);
const schemas = Object.fromEntries(Object.entries(wizard.components.schemas).map(([name,schema])=>[`Wizard${name}`, remap(schema)]));
schemas.ProducerLocalizationRequest = remap(wizard.components.schemas.LocalizationRequest);
schemas.ProducerRun = document.getIn(["components","schemas","ProducerRun"]).toJSON();
schemas.ProducerRun.properties.status.description = "archived_unknown preserves an unresolved provider result and forbids resume.";
schemas.ProducerRun.allOf = [{if:{properties:{status:{const:"archived_unknown"}},required:["status"]},then:{properties:{resume_allowed:{const:false},outcome:{type:"null"}}}}];
schemas.ProducerRun.properties.execution_mode = {type:"string",enum:["repair","verify_only"],readOnly:true};
for (const key of ["translation_coverage","judge_coverage","payment_outcome"]) schemas.ProducerRun.properties[key] = {type:"object",readOnly:true,additionalProperties:true};
const edits = [];
function patchMap(keys, updates, indentation) {
  const map = document.getIn(keys);
  let appended = "";
  for (const [name, value] of Object.entries(updates)) {
    const text = stringify({[name]:value}, {lineWidth:0}).split("\n").map(line=>line ? " ".repeat(indentation)+line : line).join("\n");
    const entry = map.items.find(item=>item.key.value === name);
    if (!entry) appended += text;
    else edits.push([original.lastIndexOf("\n", entry.key.range[0])+1, entry.value.range[1], text]);
  }
  if (appended) edits.push([map.range[1],map.range[1],appended]);
}
patchMap(["components","schemas"], schemas, 4);
patchMap(["paths"], Object.fromEntries(Object.entries(wizard.paths).map(([name,value])=>[name,remap(value)])), 2);
for (const [start,end,text] of edits.sort((a,b)=>b[0]-a[0])) original = original.slice(0,start)+text+original.slice(end);
writeFileSync(path, original);
