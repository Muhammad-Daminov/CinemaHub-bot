/** Promo code list and generator — creation goes through the same service the bot's /createpromo uses. */
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminPromoCode, PromoDiscountType } from "../types/admin";
import {
  Badge,
  Button,
  CardShell,
  EmptyState,
  Field,
  Notice,
  SectionTitle,
  Select,
  TextInput,
} from "./ui";

const DISCOUNT_TYPES: { value: PromoDiscountType; label: string }[] = [
  { value: "fixed_amount_balance", label: "Hisobga pul" },
  { value: "premium_days", label: "Premium kun" },
  { value: "percentage_discount", label: "Foizli chegirma" },
];

const discountLabel = (value: PromoDiscountType) =>
  DISCOUNT_TYPES.find((item) => item.value === value)?.label ?? value;

export function PromoPanel() {
  const [codes, setCodes] = useState<AdminPromoCode[]>([]);
  const [discountType, setDiscountType] = useState<PromoDiscountType>("fixed_amount_balance");
  const [value, setValue] = useState("");
  const [maxUses, setMaxUses] = useState("");
  const [validDays, setValidDays] = useState("");
  const [customCode, setCustomCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCodes(await adminApi.listPromo());
    } catch {
      setCodes([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    const parsedValue = Number(value);
    if (!value.trim() || Number.isNaN(parsedValue)) {
      setError("Qiymat noto'g'ri.");
      return;
    }
    try {
      await adminApi.createPromo({
        discount_type: discountType,
        value: parsedValue,
        code: customCode.trim() || null,
        max_uses: maxUses.trim() ? Number(maxUses) : null,
        valid_days: validDays.trim() ? Number(validDays) : null,
      });
      setValue("");
      setMaxUses("");
      setValidDays("");
      setCustomCode("");
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yaratishda xatolik.");
    }
  };

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle>Promokodlar</SectionTitle>
        {codes.length === 0 ? (
          <EmptyState message="Promokod yo'q." />
        ) : (
          <ul className="space-y-2">
            {codes.map((promo) => (
              <li key={promo.id} className="rounded-xl border border-surface-hi bg-surface p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm font-semibold text-marquee">
                      {promo.code}
                    </p>
                    <p className="font-body text-xs text-ink-dim">
                      {discountLabel(promo.discount_type)} · {promo.value}
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-ink-dim">
                      {promo.current_uses}/{promo.max_uses ?? "∞"}
                      {promo.valid_until ? ` · ${promo.valid_until.slice(0, 10)}` : ""}
                    </p>
                  </div>
                  <Badge active={promo.is_active}>{promo.is_active ? "Faol" : "O'chiq"}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>Yangi promokod</SectionTitle>
        <CardShell>
          <div className="space-y-2">
            <Field label="Turi">
              <Select<PromoDiscountType>
                value={discountType}
                onChange={setDiscountType}
                options={DISCOUNT_TYPES}
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Qiymat">
                <TextInput value={value} onChange={setValue} type="number" />
              </Field>
              <Field label="Limit (bo'sh = ∞)">
                <TextInput value={maxUses} onChange={setMaxUses} type="number" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Muddat (kun)">
                <TextInput value={validDays} onChange={setValidDays} type="number" />
              </Field>
              <Field label="Kod (ixtiyoriy)">
                <TextInput value={customCode} onChange={setCustomCode} mono />
              </Field>
            </div>
            <Button full onClick={handleCreate}>
              Yaratish
            </Button>
            {error && <Notice message={error} tone="error" />}
          </div>
        </CardShell>
      </section>
    </div>
  );
}
