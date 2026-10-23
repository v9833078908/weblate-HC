import {render, screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect, it, vi} from "vitest";
import {QualityModeSelection} from "./QualityModeSelection";

it("selects verification with a semantic radio and explains separate judge cost", async () => {
  const change = vi.fn();
  render(<QualityModeSelection value="repair" onChange={change} />);
  await userEvent.setup().click(screen.getByRole("radio", {name:"Только проверка — без изменения переводов"}));
  expect(change).toHaveBeenCalledWith("verify_only");
  expect(screen.getByText(/Оценка двумя судьями/)).toBeVisible();
});
