"use client";

import * as React from "react";
import { langName } from "@/components/console/upload-parts";

export const UniCtx = React.createContext(null);
export const useUni = () => React.useContext(UniCtx);

// Studio preset (group ids pt/es/zh need a region decision)
export const PRESET = [
  { code: "en", name: "Английский" },
  { code: "de", name: "Немецкий" },
  { code: "fr", name: "Французский" },
  { code: "es", name: "Испанский", group: true },
  { code: "pt", name: "Португальский", group: true },
  { code: "tr", name: "Турецкий" },
  { code: "ja", name: "Японский" },
  { code: "ko", name: "Корейский" },
  { code: "zh", name: "Китайский", group: true },
];

export const REGION = {
  pt: [
    { code: "pt_BR", label: "Бразилия — pt-BR" },
    { code: "pt_PT", label: "Португалия — pt-PT" },
  ],
  es: [
    { code: "es", label: "Испания — es-ES" },
    { code: "es_419", label: "Латинская Америка — es-419" },
  ],
  zh: [
    { code: "zh_Hans", label: "Упрощённый — zh-Hans" },
    { code: "zh_Hant", label: "Традиционный — zh-Hant" },
  ],
};

const GROUP_ID = new Set(["pt", "es", "zh"]);
const REGION_OF = {
  pt_BR: "pt",
  pt_PT: "pt",
  es: "es",
  es_419: "es",
  zh_Hans: "zh",
  zh_Hant: "zh",
  zh_Hant_HK: "zh",
};
const REGION_LABEL = {
  pt_BR: "pt-BR",
  pt_PT: "pt-PT",
  es: "es-ES",
  es_419: "es-419",
  zh_Hans: "zh-Hans",
  zh_Hant: "zh-Hant",
};
export const regionLabel = (code) => REGION_LABEL[code] || code;

// Build the working target-language list from analysis + demo seed
export function initLanguages(analysis) {
  const src = analysis.source_language;
  const map = new Map();
  const ensure = (key, base) => {
    if (!map.has(key)) map.set(key, base);
  };

  (analysis.languages_found || []).forEach((l) => {
    if (l.is_source || l.code === src) return;
    const group = REGION_OF[l.code] || null;
    ensure(group || l.code, {
      key: group || l.code,
      group,
      code: l.code,
      name: l.name || langName(l.code),
      source: "file",
      include: true,
      has_translation: !!l.has_translation,
      fill: l.fill,
    });
  });

  (analysis._seed_languages || []).forEach((code) => {
    if (GROUP_ID.has(code)) {
      ensure(code, {
        key: code,
        group: code,
        code: null,
        name: PRESET.find((p) => p.code === code)?.name || code,
        source: "preset",
        include: true,
        has_translation: false,
      });
    } else {
      const group = REGION_OF[code] || null;
      ensure(group || code, {
        key: group || code,
        group,
        code,
        name: langName(code),
        source: "preset",
        include: true,
        has_translation: (analysis._seed_translated || []).includes(code),
      });
    }
  });

  const tr = new Set(analysis._seed_translated || []);
  map.forEach((e) => {
    if (e.code && tr.has(e.code)) e.has_translation = true;
  });
  return Array.from(map.values());
}

// codes that will actually be translated (region resolved, source excluded)
export function finalTargetCodes(languages, source) {
  return (languages || [])
    .filter((l) => l.include && l.code && l.code !== source)
    .map((l) => l.code);
}
