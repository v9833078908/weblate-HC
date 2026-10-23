import { render, screen } from "@testing-library/react";
import { type AxeResults, run as axe } from "axe-core";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { ProducerProjectDetail } from "../api/client";
import { ProjectOverview } from "./ProjectOverview";

const project: ProducerProjectDetail = {
  slug: "pirate-ships",
  name: "Pirate Ships",
  profile_state: "configured",
  advanced_url: "/weblate/projects/pirate-ships/",
  contents: [
    {
      id: "units",
      type: "loc_kit",
      label: "Units",
      keys: 20,
      uploadable: true,
      exportable: true,
      advanced_url: "/weblate/projects/pirate-ships/units/",
    },
  ],
  languages: [
    {
      code: "ru",
      name: "Russian",
      strings: 20,
      status: "translating",
      decisions: 0,
      in_kit: true,
    },
  ],
};

function renderOverview() {
  return render(
    <MemoryRouter>
      <ProjectOverview project={project} />
    </MemoryRouter>,
  );
}

describe("ProjectOverview", () => {
  it("uses server-provided Advanced links and opens the real localization route", () => {
    renderOverview();

    expect(
      screen.getByRole("link", { name: "Подготовить локализацию" }),
    ).toHaveAttribute("href", "/projects/pirate-ships/localize");
    expect(
      screen.getByRole("link", { name: "Скачать результаты" }),
    ).toHaveAttribute("href", "/projects/pirate-ships/download");
    expect(
      screen.getAllByRole("link", { name: "Открыть в Weblate" })[0],
    ).toHaveAttribute("href", "/weblate/projects/pirate-ships/units/");
    expect(screen.getByText("Russian")).toBeVisible();
  });

  it("has no serious automated accessibility violations", async () => {
    const { container } = renderOverview();
    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });

  it("does not offer export for content without producer loc-kit provenance", () => {
    render(
      <MemoryRouter>
        <ProjectOverview
          project={{
            ...project,
            contents: [{ ...project.contents[0], exportable: false }],
          }}
        />
      </MemoryRouter>,
    );

    expect(
      screen.queryByRole("link", { name: "Скачать результаты" }),
    ).not.toBeInTheDocument();
  });
});
