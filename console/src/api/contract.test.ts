import { describe, expect, it } from "vitest";

import {
  validateProducerFixtures,
  validateProfilePreparation,
  validateProjectDetail,
  validateProjectSummary,
} from "./contract";

describe("Producer OpenAPI runtime contract", () => {
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
