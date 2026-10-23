import russian from "./locales/ru.json";

declare global {
  var gettext: ((message: string) => string) | undefined;
}

export function t(message: string): string {
  const translated = Object.hasOwn(russian, message) ? (russian as Record<string, string>)[message] : undefined;
  if (translated) return translated;
  const http = message.match(/^(External Weblate returned|BDHC request returned|Profile service returned) HTTP (\d{3})$/);
  if (http) {
    const service = http[1].startsWith("External") ? "Внешний Weblate" : http[1].startsWith("BDHC") ? "БДХК" : "Сервис подготовки инструкций";
    return `${service} вернул код HTTP ${http[2]}.`;
  }
  const shorten = message.match(/^(.+): shorten the BDHC value manually$/);
  if (shorten) return `${shorten[1]}: сократите значение из БДХК вручную.`;
  const profiles = message.match(/^(.+): multiple locale profiles require selection$/);
  if (profiles) return `${profiles[1]}: найдено несколько языковых профилей, требуется выбор.`;
  const guide = message.match(/^(.+): no verified local locale guide$/);
  if (guide) return `${guide[1]}: проверенной локальной справки нет; проверьте языковые инструкции вручную.`;
  return globalThis.gettext?.(message) ?? message;
}
