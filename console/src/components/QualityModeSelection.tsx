import { t } from "../i18n";

export function QualityModeSelection({value, onChange}: {
  value: "repair" | "verify_only";
  onChange: (value: "repair" | "verify_only") => void;
}) {
  return <fieldset className="quality-policy">
    <legend>{t("Quality execution mode")}</legend>
    <label className="check-row"><input type="radio" name="quality-execution-mode" value="repair"
      checked={value === "repair"} onChange={() => onChange("repair")} />{t("Check and repair verified issues")}</label>
    <label className="check-row"><input type="radio" name="quality-execution-mode" value="verify_only"
      checked={value === "verify_only"} onChange={() => onChange("verify_only")} />{t("Verify only — without changing translations")}</label>
    <p className="helper-note">{t("Two-judge evaluation can have a separate cost. Verification generates no repair candidates and does not change translations, states or glossary.")}</p>
  </fieldset>;
}
