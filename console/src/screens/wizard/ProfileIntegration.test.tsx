import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { ProducerApi, ProducerProfile } from "../../api/client";
import { ProfileIntegration } from "./ProfileIntegration";

const profile: ProducerProfile = {
  revision: "revision", answers: {}, summary: "", missing_required: [],
  profile_state: "missing", engine: "openrouter", judge_context_changed: false,
};

it("uses full regional language names while retaining exact codes in instructions", async () => {
  const user = userEvent.setup();
  const api = {setManualProfile: vi.fn().mockResolvedValue(profile)} as unknown as ProducerApi;
  render(<ProfileIntegration api={api} slug="local" projectName="Game" source="ru" targets={["pt_BR", "es_MX"]} profile={profile} onApplied={vi.fn()}/>);
  await user.click(screen.getByText("Готовые инструкции переводчику — ручной ввод"));
  expect((screen.getByLabelText("Роль переводчика") as HTMLTextAreaElement).value).toContain("на португальский (Бразилия), испанский (Мексика)");
  await user.type(screen.getByLabelText("Инструкции для языка Португальский (Бразилия) (pt_BR)"), "Brazilian usage");
  await user.click(screen.getByRole("button",{name:"Сохранить инструкции"}));
  expect(api.setManualProfile).toHaveBeenCalledWith("local","revision",expect.objectContaining({language_instructions:{pt_BR:"Brazilian usage"}}));
});

it("refreshes language inserts in saved template instructions for a new draft without losing edits", async () => {
  const user = userEvent.setup();
  const savedPersona = "Ты профессиональный локализатор видеоигр. Переводи тексты Lord Ambermaze с русского на немецкий: интерфейс, диалоги и игровые описания. Учитывай сказочную атмосферу. Не переводи имена.";
  const savedProfile = { ...profile, generated: { persona: savedPersona, style: "Мой сказочный стиль", language_instructions: { de: "Немецкое правило" } } };
  const api = { setManualProfile: vi.fn().mockResolvedValue(savedProfile) } as unknown as ProducerApi;
  const props = { api, slug: "local", projectName: "Lord Ambermaze", source: "ru", targets: ["en"], profile: savedProfile, onApplied: vi.fn() };
  const { rerender } = render(<ProfileIntegration {...props} source={null} targets={[]} />);
  expect(screen.getByLabelText("Роль переводчика")).toHaveValue(savedPersona);
  rerender(<ProfileIntegration {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Роль переводчика")).toHaveValue(savedPersona.replace("на немецкий:", "на английский:")));
  expect(screen.getByLabelText("Стиль перевода")).toHaveValue("Мой сказочный стиль");
  await user.click(screen.getByText("Готовые инструкции переводчику — ручной ввод"));
  await user.type(screen.getByLabelText("Роль переводчика"), " Сохраняй юмор.");
  rerender(<ProfileIntegration {...props} projectName="New $& game" source="en" targets={["fr", "ja"]} />);
  const updatedPersona = savedPersona.replace("Lord Ambermaze с русского на немецкий", () => "New $& game с английского на французский, японский") + " Сохраняй юмор.";
  await waitFor(() => expect(screen.getByLabelText("Роль переводчика")).toHaveValue(updatedPersona));
  await user.click(screen.getByRole("button", { name: "Сохранить инструкции" }));
  expect(api.setManualProfile).toHaveBeenCalledWith("local", "revision", { persona: updatedPersona, style: "Мой сказочный стиль", language_instructions: {} });
});

it("fills editable instructions with project name and selected languages and saves them", async () => {
  const user = userEvent.setup();
  const api = { setManualProfile: vi.fn().mockResolvedValue(profile) } as unknown as ProducerApi;
  render(<ProfileIntegration api={api} slug="local" projectName="Lord Ambermaze" source="ru" targets={["de"]} profile={profile} onApplied={vi.fn()} />);
  await user.click(screen.getByText("Готовые инструкции переводчику — ручной ввод"));
  expect(screen.getByLabelText("Роль переводчика")).toHaveValue("Ты профессиональный локализатор видеоигр. Переводи тексты Lord Ambermaze с русского на немецкий: интерфейс, диалоги и игровые описания. Учитывай контекст приключенческой игры с головоломками.");
  expect(screen.getByLabelText("Стиль перевода")).toHaveValue("Сохраняй атмосферу игры, юмор и драматические оттенки оригинала. Интерфейс переводи кратко и понятно, диалоги — естественно, с учётом характера персонажа. Соблюдай единообразие терминов. Не добавляй новых фактов. Сохраняй игровые теги, переменные, числа и разделители строк.");
  await user.clear(screen.getByLabelText("Стиль перевода"));
  await user.type(screen.getByLabelText("Стиль перевода"), "Мой стиль");
  await user.click(screen.getByRole("button", { name: "Сохранить инструкции" }));
  expect(api.setManualProfile).toHaveBeenCalledWith("local", "revision", expect.objectContaining({ style: "Мой стиль", persona: expect.stringContaining("Lord Ambermaze с русского на немецкий"), language_instructions: {} }));
});

it("updates untouched defaults but preserves edited fields and saved instructions", async () => {
  const user = userEvent.setup();
  const api = {} as ProducerApi;
  const props = { api, slug: "local", projectName: "Dead Shell", source: "en", targets: ["de"], profile, onApplied: vi.fn() };
  const { rerender } = render(<ProfileIntegration {...props} />);
  expect((screen.getByLabelText("Роль переводчика") as HTMLTextAreaElement).value).toContain("Dead Shell с английского на немецкий");
  rerender(<ProfileIntegration {...props} targets={["fr", "ja"]} />);
  await waitFor(() => expect((screen.getByLabelText("Роль переводчика") as HTMLTextAreaElement).value).toContain("на французский, японский"));
  await user.click(screen.getByText("Готовые инструкции переводчику — ручной ввод"));
  await user.clear(screen.getByLabelText("Роль переводчика"));
  await user.type(screen.getByLabelText("Роль переводчика"), "Моя роль");
  rerender(<ProfileIntegration {...props} projectName="Another game" targets={["es"]} profile={{ ...profile, revision: "changed" }} />);
  expect(screen.getByLabelText("Роль переводчика")).toHaveValue("Моя роль");
  rerender(<ProfileIntegration {...props} key="saved" profile={{ ...profile, generated: { persona: "Сохранённая роль", style: "Сохранённый стиль", language_instructions: { de: "Особое правило" } } }} />);
  expect(screen.getByLabelText("Роль переводчика")).toHaveValue("Сохранённая роль");
  expect(screen.getByLabelText("Стиль перевода")).toHaveValue("Сохранённый стиль");
});
