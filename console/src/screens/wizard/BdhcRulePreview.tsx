// Copyright © HCGameLoc
// SPDX-License-Identifier: GPL-3.0-or-later
import { t } from "../../i18n";

const labels: Record<string, string> = {
  is_disabled: "Персонаж отключён", sort_order: "Порядок отображения",
  genre: "Жанр", setting: "Мир игры", player_role: "Роль игрока", audience: "Аудитория",
  register_ui: "Стиль интерфейса", register_dialogue: "Стиль диалогов", profanity: "Ненормативная лексика",
  locale: "Язык и регион", tone: "Тон и атмосфера", register: "Стиль речи",
  player_address: "Обращение к игроку", player_address_note: "Примечание к обращению",
  humor: "Юмор", profanity_policy: "Ненормативная лексика", profanity_fidelity: "Соответствие лексике оригинала",
  emoji_policy: "Эмодзи", emoji_source_fidelity: "Соответствие эмодзи оригинала", emoji_policy_note: "Примечание к эмодзи",
  forbidden_rules: "Ограничения", rule: "Правило", applies_to: "К чему относится",
  uppercase_terms: "Термины прописными буквами", names_policy: "Имена и названия",
  notification_style: "Стиль уведомлений", patchnotes_style: "Стиль описания обновлений",
  locale_notes: "Примечания для языка", has_character_voices: "Есть голоса персонажей",
  not_applicable_fields: "Неприменимые поля", dnt_brand_terms: "Непереводимые названия брендов",
  voices: "Голоса персонажей", character_key: "Ключ персонажа", name: "Имя", notes: "Примечания",
  speech_style_codes: "Особенности речи", profanity_level: "Уровень ненормативной лексики",
  example_lines: "Примеры реплик", source: "Источник", title_id: "Идентификатор карточки",
  profile_id: "Идентификатор профиля", voice_id: "Идентификатор голоса", created_at: "Создано", updated_at: "Обновлено",
};
const values: Record<string, string> = {
  informal: "Неформальный", formal: "Формальный", neutral: "Нейтральный",
  uncensored: "Без цензуры", censored: "С цензурой", none: "Отсутствует",
  match_source: "Как в исходном тексте", never: "Не использовать", always: "Использовать",
  transliterate: "Транслитерировать", translate: "Переводить", keep: "Сохранять оригинал",
};
const enumFields = new Set([
  "register", "register_ui", "register_dialogue", "player_address", "profanity", "profanity_policy",
  "profanity_fidelity", "emoji_policy", "emoji_source_fidelity", "names_policy", "profanity_level", "speech_style_codes",
]);
const technicalFields = new Set(["source", "title_id", "profile_id", "voice_id", "created_at", "updated_at"]);

export function bdhcFieldLabel(field: string): string {
  return t(labels[field] ?? field.replaceAll("_", " "));
}

export function BdhcRuleValue({ value, field = "" }: { value: unknown; field?: string }) {
  if (value === null || value === undefined || value === "") return <span>{t("Не задано")}</span>;
  if (typeof value === "boolean") return <span>{value ? t("Да") : t("Нет")}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span>{t("Не задано")}</span>;
    if (field === "voices") return <ul className="bdhc-rule-items">{value.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return <li key={index}><BdhcRuleValue value={item} /></li>;
      const { name, ...voice } = item as Record<string, unknown>;
      return <li key={index}><details className="bdhc-character"><summary>{typeof name === "string" && name ? name : `${t("Персонаж")} ${index + 1}`}</summary>
        {name && typeof name !== "string" ? <BdhcRuleValue value={{ name }} /> : null}
        <BdhcRuleValue value={voice} />
      </details></li>;
    })}</ul>;
    return <ul className="bdhc-rule-items">{value.map((item, index) => <li key={index}><BdhcRuleValue value={item} field={field} /></li>)}</ul>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return <span>{t("Правила не заданы")}</span>;
    const meaningful = entries.filter(([key]) => !technicalFields.has(key));
    const technical = entries.filter(([key]) => technicalFields.has(key));
    const renderEntries = (items: Array<[string, unknown]>) => <dl className="bdhc-rule-fields">{items.map(([key, item]) => <div key={key}>
      <dt>{bdhcFieldLabel(key)}</dt><dd><BdhcRuleValue value={item} field={key} /></dd>
    </div>)}</dl>;
    return <>{renderEntries(meaningful)}{technical.length ? <details className="bdhc-technical"><summary>{t("Служебные сведения правила")}</summary>{renderEntries(technical)}</details> : null}</>;
  }
  const text = String(value);
  if (field === "applies_to" || field === "not_applicable_fields") return <span>{bdhcFieldLabel(text)}</span>;
  return <span>{enumFields.has(field) && values[text] ? t(values[text]) : text}</span>;
}
