import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { LanguageSelection } from "./LanguageSelection";
import { languageLabel, resolveLanguageInput } from "./languages";

const catalog = ["ru", "en", "fr", "it", "de", "zh_Hans", "ko", "ja", "pt_BR", "es_MX", "pl", "cs", "nl", "pt", "es", "zh_Hant", "sw"].map(code => ({code, name: code === "sw" ? "Swahili" : code}));
function Harness({kit = ["ru", "en", "fr", "it", "de"], remote = ["pt-br", "nl"], available = catalog, initialTargets = ["de"], error = ""}: {kit?: string[]; remote?: string[]; available?: typeof catalog; initialTargets?: string[]; error?: string}) {
  const [source, setSource] = useState<string | null>("ru");
  const [targets, setTargets] = useState(initialTargets);
  return <><LanguageSelection source={source} targets={targets} kitCodes={kit} remoteCodes={remote} catalog={available} catalogError={error} busy={false} onSourceChange={setSource} onTargetsChange={setTargets}/><output aria-label="Saved selection">{JSON.stringify({source, targets})}</output></>;
}

describe("Language selection", () => {
  it("adds Traditional Chinese separately from an already selected Simplified Chinese and reports the result next to the input", async () => {
    const user = userEvent.setup();
    render(<Harness initialTargets={["zh_Hans"]}/>);
    const right = screen.getByRole("group", {name: "Добавить языки перевода"});
    const input = screen.getByRole("textbox", {name: "Добавить свой язык"});
    await user.type(input, "Традиционный китайский");
    await user.click(screen.getByRole("button", {name: "Добавить"}));
    expect(within(right).getByRole("checkbox", {name: /\(zh_Hant\)/})).toBeChecked();
    expect(within(right).getByRole("checkbox", {name: /\(zh_Hans\)/})).toBeChecked();
    const feedback = screen.getByRole("status", {name: "Добавление языка"}).parentElement!;
    expect(screen.getByRole("status", {name: "Добавление языка"})).toHaveTextContent(/Добавлен и выбран.*zh_Hant/);
    expect(feedback.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(input).toHaveValue("");
  });

  it("asks which Chinese script is intended and leaves current targets unchanged until a choice is made", async () => {
    const user = userEvent.setup();
    render(<Harness initialTargets={["zh_Hans"]}/>);
    const input = screen.getByRole("textbox", {name: "Добавить свой язык"});
    await user.type(input, "Китайский{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent(/Найдено несколько вариантов/);
    expect(screen.getByRole("status", {name:"Saved selection"})).toHaveTextContent('"targets":["zh_Hans"]');
    const traditional = screen.getByRole("button", {name:"Китайский (традиционный) (zh_Hant)"});
    expect(traditional.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(traditional);
    expect(screen.getByRole("checkbox", {name:/\(zh_Hant\)/})).toBeChecked();
    expect(screen.getByRole("checkbox", {name:/\(zh_Hans\)/})).toBeChecked();
  });
  it("has one card per canonical language across the kit, recommendations, remote and saved targets", () => {
    render(<Harness/>);
    const kit = screen.getByRole("group", {name: "Языки, уже присутствующие в лок-ките"});
    const added = screen.getByRole("group", {name: "Добавить языки перевода"});
    for (const code of ["ru", "en", "fr", "it", "de"]) {
      expect(within(kit).getAllByRole("checkbox", {name: new RegExp(`\\(${code}\\)`) })).toHaveLength(1);
      expect(within(added).queryByRole("checkbox", {name: new RegExp(`\\(${code}\\)`) })).toBeNull();
    }
    expect(screen.getAllByRole("checkbox")).toHaveLength(13);
    expect(screen.getAllByRole("checkbox", {name: /\(pt_BR\)/})).toHaveLength(1);
    expect(screen.getByText(/Наличие языка не означает/)).toBeVisible();
    expect(screen.getByRole("checkbox", {name: /Русский \(ru\)/})).toBeDisabled();
    expect(screen.getByRole("checkbox", {name: /Португальский.*pt_BR/})).toHaveAccessibleDescription(/Уже есть в проекте Weblate/);
  });

  it("adds by Russian name, code, Enter or button without duplicating old targets", async () => {
    const user = userEvent.setup();
    render(<Harness kit={["ru", "pt"]} initialTargets={["pt-br"]}/>);
    const input = screen.getByRole("textbox", {name: "Добавить свой язык"});
    await user.type(input, "португальский бразильский{Enter}");
    expect(screen.getAllByRole("checkbox", {name: /\(pt_BR\)/})).toHaveLength(1);
    await user.click(screen.getByRole("checkbox", {name: /\(pt_BR\)/}));
    await user.type(input, "PT-br");
    await user.click(screen.getByRole("button", {name: "Добавить"}));
    expect(screen.getByRole("checkbox", {name: /\(pt_BR\)/})).toBeChecked();
    await user.type(input, "испанский мексиканский{Enter}");
    expect(screen.getByRole("status", {name: "Saved selection"})).toHaveTextContent('"targets":["pt_BR","es_MX"]');
    expect(screen.getByRole("checkbox", {name: /^Португальский \(pt\)/})).not.toBeChecked();
    await user.type(input, "несуществующий{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent(/Не удалось распознать/);
    await user.clear(input);
    await user.type(input, "swahili{Enter}");
    expect(screen.getByRole("checkbox", {name: /Суахили \(sw\)/})).toBeChecked();
    await user.selectOptions(screen.getByLabelText("Язык, с которого переводить"), "pt");
    expect(screen.getByRole("checkbox", {name: /^Португальский \(pt\)/})).toBeDisabled();
  });

  it("keeps existing choices when the catalog is unavailable and blocks unverified additions", async () => {
    render(<Harness available={[]} error="Справочник языков недоступен"/>);
    expect(screen.getByRole("checkbox", {name: /Немецкий \(de\)/})).toBeChecked();
    expect(screen.getByRole("textbox", {name: "Добавить свой язык"})).toBeDisabled();
    expect(screen.getByRole("checkbox", {name: /\(pt_BR\)/})).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/недоступен/);
  });

  it("does not guess ambiguous names and uses a full Weblate name for rare codes", () => {
    expect(resolveLanguageInput("Same name", [{code:"aa",name:"Same name"},{code:"ab",name:"Same name"}])).toHaveLength(2);
    expect(languageLabel("x-test", [{code:"x-test",name:"A complete rare language name"}])).toBe("A complete rare language name");
    expect(resolveLanguageInput("Китайский", catalog).map(row=>row.code)).toEqual(["zh_Hans", "zh_Hant"]);
    expect(resolveLanguageInput("Традиционный китайский", catalog)[0].code).toBe("zh_Hant");
    expect(resolveLanguageInput("zh-hant", catalog)[0].code).toBe("zh_Hant");
  });
});
