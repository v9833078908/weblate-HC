import Ajv, { type ErrorObject, type ValidateFunction } from "ajv";
import { parse } from "yaml";

import openapiSource from "../../../docs/specs/openapi.yaml?raw";
import meFixture from "./mock/fixtures/me.json";
import createdProjectFixture from "./mock/fixtures/project-created.json";
import projectDetailFixture from "./mock/fixtures/project-detail.json";
import projectsFixture from "./mock/fixtures/projects.json";

interface OpenApiDocument {
  components: {
    schemas: Record<string, Record<string, unknown>>;
  };
}

export interface ContractIssue extends ErrorObject {
  fixture?: string;
}

const openapi = parse(openapiSource) as OpenApiDocument;
for (const schemaName of [
  "ProducerProjectSummary",
  "ProducerProjectDetail",
  "ProducerProjectLanguage",
  "ProducerProjectContent",
]) {
  openapi.components.schemas[schemaName] = {
    ...openapi.components.schemas[schemaName],
    additionalProperties: false,
  };
}
const ajv = new Ajv({ allErrors: true, strict: false, validateFormats: false });
ajv.addSchema(openapi, "producer-openapi");

function schemaValidator(name: string): ValidateFunction {
  return ajv.compile({
    $ref: `producer-openapi#/components/schemas/${name}`,
  });
}

const validateMe = schemaValidator("ProducerMe");
const validateProjects = schemaValidator("PaginatedProducerProjectSummaryList");
const validateProjectSummarySchema = schemaValidator("ProducerProjectSummary");
const validateProjectDetailSchema = schemaValidator("ProducerProjectDetail");

function collectErrors(
  validator: ValidateFunction,
  value: unknown,
  fixture?: string,
): ContractIssue[] {
  if (validator(value)) {
    return [];
  }
  return (validator.errors ?? []).map((error) => ({ ...error, fixture }));
}

export function validateProjectSummary(value: unknown): ContractIssue[] {
  return collectErrors(validateProjectSummarySchema, value);
}

export function validateProjectDetail(value: unknown): ContractIssue[] {
  return collectErrors(validateProjectDetailSchema, value);
}

export function validateProducerFixtures(): ContractIssue[] {
  return [
    ...collectErrors(validateMe, meFixture, "me.json"),
    ...collectErrors(validateProjects, projectsFixture, "projects.json"),
    ...collectErrors(
      validateProjectSummarySchema,
      createdProjectFixture,
      "project-created.json",
    ),
    ...collectErrors(
      validateProjectDetailSchema,
      projectDetailFixture,
      "project-detail.json",
    ),
  ];
}

export function validateProfilePreparation(value: unknown): ContractIssue[] {
  return collectErrors(schemaValidator("ProducerProfilePreparation"), value);
}
