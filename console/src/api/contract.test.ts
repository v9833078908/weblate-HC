import { describe, expect, it } from "vitest";
import Ajv from "ajv";
import { parse } from "yaml";
import openapiSource from "../../../docs/specs/openapi.yaml?raw";

import {
  validateProducerFixtures,
  validateProfilePreparation,
  validateProjectDetail,
  validateProjectSummary,
} from "./contract";

describe("Producer OpenAPI runtime contract", () => {
  it("forbids resume in the archived-run API contract", () => {
    const schema = parse(openapiSource);
    const ajv = new Ajv({ strict: false, validateFormats: false });
    ajv.addSchema(schema, "producer");
    const validate = ajv.compile({ $ref: "producer#/components/schemas/ProducerRun" });
    const run = { id: "11111111-1111-1111-1111-111111111111", status: "archived_unknown",
      requested_mode: "translate", created: "2026-10-23T00:00:00Z", started: null, finished: null,
      summary: {}, failure: "", warnings: [], stages: {}, liveness: "archived_unknown", outcome: null,
      resume_allowed: false, resume_reason: null, preparation_phase: "translate", coverage: {} };
    expect(validate(run)).toBe(true);
    expect(validate({ ...run, resume_allowed: true })).toBe(false);
  });
  it("validates all fixtures against OpenAPI schemas", () => {
    expect(validateProducerFixtures()).toEqual([]);
  });

  it("rejects a fixture with an extra field or missing required field", () => {
    expect(
      validateProjectSummary({ name: "Broken", extra: true }),
    ).toContainEqual(
      expect.objectContaining({
        keyword: expect.stringMatching(/required|additionalProperties/),
      }),
    );
  });

  it("rejects missing, extra, and invalid project-detail fields", () => {
    expect(
      validateProjectDetail({
        slug: "broken",
        name: "Broken",
        languages: [],
        contents: [],
        profile_state: "missing",
        advanced_url: "https://weblate.test/projects/broken/",
        extra: true,
      }),
    ).toContainEqual(expect.objectContaining({ keyword: "additionalProperties" }));

    expect(
      validateProjectDetail({
        slug: "broken",
        name: "Broken",
        languages: [],
        contents: [],
        profile_state: "missing",
      }),
    ).toContainEqual(expect.objectContaining({ keyword: "required" }));

    expect(
      validateProjectDetail({
        slug: "broken",
        name: "Broken",
        languages: [
          {
            code: "de",
            name: "Немецкий",
            strings: 1,
            status: "excellent",
            decisions: 0,
            in_kit: false,
          },
        ],
        contents: [],
        profile_state: "missing",
        advanced_url: "https://weblate.test/projects/broken/",
      }),
    ).toContainEqual(expect.objectContaining({ keyword: "enum" }));
  });
});

it("validates saved profile preparation and rejects unknown job states", () => {
  const ready={job_id:"job",input_key:"key",state:"ready",result:{persona:"Role",style:"Style",language_instructions:{zh_Hant:"Traditional"}},proposal:null,generation_mode:"agent",warnings:[],auto_saved:true,profile_revision:"rev"};
  expect(validateProfilePreparation(ready)).toEqual([]);
  expect(validateProfilePreparation({...ready,state:"complete"})).not.toEqual([]);
});
