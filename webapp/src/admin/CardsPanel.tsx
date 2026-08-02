/** Payment cards shown to users when they top up. */
import { Power } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminCard } from "../types/admin";
import {
  Badge,
  Button,
  CardShell,
  EmptyState,
  Field,
  IconButton,
  Notice,
  SectionTitle,
  TextInput,
} from "./ui";

export function CardsPanel() {
  const [cards, setCards] = useState<AdminCard[]>([]);
  const [cardNumber, setCardNumber] = useState("");
  const [holderName, setHolderName] = useState("");
  const [bankName, setBankName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCards(await adminApi.listCards());
    } catch {
      setCards([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!cardNumber.trim() || !holderName.trim()) {
      setError("Karta raqami va egasi kiritilishi shart.");
      return;
    }
    try {
      await adminApi.createCard({
        card_number: cardNumber.trim(),
        holder_name: holderName.trim(),
        bank_name: bankName.trim() || null,
      });
      setCardNumber("");
      setHolderName("");
      setBankName("");
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Qo'shishda xatolik.");
    }
  };

  const handleToggle = async (id: number) => {
    await adminApi.toggleCard(id).catch(() => undefined);
    load();
  };

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle>Kartalar</SectionTitle>
        {cards.length === 0 ? (
          <EmptyState message="Karta qo'shilmagan." />
        ) : (
          <ul className="space-y-2">
            {cards.map((card) => (
              <li key={card.id} className="rounded-xl border border-surface-hi bg-surface p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-ink">{card.card_number}</p>
                    <p className="truncate font-body text-xs text-ink-dim">
                      {card.holder_name}
                      {card.bank_name ? ` · ${card.bank_name}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Badge active={card.is_active}>{card.is_active ? "Faol" : "O'chiq"}</Badge>
                    <IconButton label="Faollik" onClick={() => handleToggle(card.id)}>
                      <Power size={15} />
                    </IconButton>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>Yangi karta</SectionTitle>
        <CardShell>
          <div className="space-y-2">
            <Field label="Karta raqami">
              <TextInput value={cardNumber} onChange={setCardNumber} placeholder="8600…" mono />
            </Field>
            <Field label="Karta egasi">
              <TextInput value={holderName} onChange={setHolderName} />
            </Field>
            <Field label="Bank (ixtiyoriy)">
              <TextInput value={bankName} onChange={setBankName} />
            </Field>
            <Button full onClick={handleCreate}>
              Qo'shish
            </Button>
            {error && <Notice message={error} tone="error" />}
          </div>
        </CardShell>
      </section>
    </div>
  );
}
