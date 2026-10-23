import type { ProducerLanguage } from "../../api/client";
import { t } from "../../i18n";

export type LanguageOption = ProducerLanguage;
export const recommendedLanguages: LanguageOption[] = [
  {code: "ru", name: "Русский"}, {code: "en", name: "Английский"},
  {code: "fr", name: "Французский"}, {code: "it", name: "Итальянский"},
  {code: "de", name: "Немецкий"}, {code: "zh_Hans", name: "Китайский (упрощённый)"},
  {code: "ko", name: "Корейский"}, {code: "ja", name: "Японский"},
  {code: "pt_BR", name: "Португальский (Бразилия)"},
  {code: "es_MX", name: "Испанский (Мексика)"}, {code: "pl", name: "Польский"},
  {code: "cs", name: "Чешский"}, {code: "nl", name: "Голландский"},
];

export const codeKey = (code: string) => code.trim().replaceAll("-", "_").toLowerCase();
export function languageCode(code: string, catalog: LanguageOption[]): string {
  return [...catalog, ...recommendedLanguages].find(row => codeKey(row.code) === codeKey(code))?.code ?? code.trim();
}
export function uniqueLanguageCodes(codes: string[], catalog: LanguageOption[]): string[] {
  const unique = new Map<string, string>();
  for (const code of codes) unique.set(codeKey(code), languageCode(code, catalog));
  return [...unique.values()];
}
export const supportedLanguage = (code: string) => /^[A-Za-z]{2,3}(?:[_-][A-Za-z0-9]{2,8})*$/.test(code);

export function languageLabel(code: string, catalog: LanguageOption[]): string {
  if (codeKey(code) === "zh_hant") return t("Китайский (традиционный)");
  const preset = recommendedLanguages.find(row => codeKey(row.code) === codeKey(code));
  if (preset) return t(preset.name);
  try {
    const name = new Intl.DisplayNames(["ru"], {type: "language", fallback: "none"}).of(code.replaceAll("_", "-"));
    if (name && codeKey(name) !== codeKey(code)) return name[0].toLocaleUpperCase("ru") + name.slice(1);
  } catch { /* Weblate has variants which Intl cannot name. */ }
  return catalog.find(row => codeKey(row.code) === codeKey(code))?.name ?? `${t("Язык")} ${code}`;
}

const nameKey = (value: string) => value.trim().toLocaleLowerCase("ru").replaceAll("ё", "е").replace(/[(),\s_-]+/g, " ").trim();
const aliases: Record<string, string> = {
  "китайский упрощенный": "zh_Hans", "упрощенный китайский": "zh_Hans", "simplified chinese": "zh_Hans",
  "китайский традиционный": "zh_Hant", "традиционный китайский": "zh_Hant", "traditional chinese": "zh_Hant",
  "португальский бразильский": "pt_BR", "бразильский португальский": "pt_BR",
  "испанский мексиканский": "es_MX", "мексиканский испанский": "es_MX",
  "голандский": "nl", "нидерландский": "nl",
};
export function resolveLanguageInput(input: string, catalog: LanguageOption[]): LanguageOption[] {
  const exactCode = catalog.filter(row => codeKey(row.code) === codeKey(input));
  if (exactCode.length) return exactCode;
  const normalized = nameKey(input);
  if (!normalized) return [];
  if (normalized === "китайский" || normalized === "chinese") {
    return catalog.filter(row => ["zh_hans", "zh_hant"].includes(codeKey(row.code)));
  }
  const alias = aliases[normalized];
  if (alias) return catalog.filter(row => row.code === alias);
  return catalog.filter(row => nameKey(row.name) === normalized || nameKey(languageLabel(row.code, catalog)) === normalized);
}
