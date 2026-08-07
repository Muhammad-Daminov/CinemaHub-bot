/**
 * Subscription plan management.
 *
 * Everything a plan is — price, duration, benefits, features, order,
 * on/off — is edited here rather than in an environment variable, which
 * is the point of Phase 4: changing a price used to need a redeploy.
 *
 * Two behaviours worth knowing while reading this:
 *   - Delete is disabled while a plan has subscribers. The backend
 *     refuses it anyway (409); disabling the control explains why before
 *     the administrator tries, rather than after.
 *   - Reordering moves one position at a time and persists immediately.
 *     Drag-and-drop inside a scrolling admin sheet on a phone is worse
 *     than two arrows.
 */
import { ArrowDown, ArrowUp, Check, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { SubscriptionFeature, SubscriptionPlan } from "../types/admin";
import {
  Button,
  EmptyState,
  Field,
  Notice,
  SectionTitle,
  TextArea,
  TextInput,
  formatMoney,
} from "./ui";

const EMPTY_DRAFT = { code: "", name: "", price: "", duration_days: "30" };

export function PlansPanel() {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [features, setFeatures] = useState<SubscriptionFeature[]>([]);
  const [draft, setDraft] = useState({ ...EMPTY_DRAFT });
  const [featureDraft, setFeatureDraft] = useState({ code: "", name: "" });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [planList, featureList] = await Promise.all([
        adminApi.listPlans(),
        // A missing MANAGE_SUBSCRIPTION_FEATURES permission must not blank
        // the whole screen — plans are still editable without it.
        adminApi.listSubscriptionFeatures().catch(() => [] as SubscriptionFeature[]),
      ]);
      setPlans(planList);
      setFeatures(featureList);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const guard = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await reload();
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Amal bajarilmadi.");
    } finally {
      setBusy(false);
    }
  };

  const addPlan = () =>
    void guard(async () => {
      await adminApi.createPlan({
        code: draft.code.trim(),
        name: draft.name.trim(),
        price: Number(draft.price || 0),
        duration_days: Number(draft.duration_days || 30),
      });
      setDraft({ ...EMPTY_DRAFT });
    });

  const move = (index: number, delta: number) => {
    const next = [...plans];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    void guard(() => adminApi.reorderPlans(next.map((p) => p.id)));
  };

  const toggleFeature = (plan: SubscriptionPlan, feature: SubscriptionFeature) => {
    const grants: Record<number, string | null> = {};
    for (const granted of plan.features) grants[granted.id] = granted.value;
    if (feature.id in grants) delete grants[feature.id];
    else grants[feature.id] = null;
    void guard(() => adminApi.setPlanFeatures(plan.id, grants));
  };

  return (
    <div className="space-y-4">
      <SectionTitle>Tariflar</SectionTitle>
      {error && <Notice message={error} tone="error" />}

      <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Kod">
            <TextInput value={draft.code} onChange={(v) => setDraft({ ...draft, code: v })} mono />
          </Field>
          <Field label="Nomi">
            <TextInput value={draft.name} onChange={(v) => setDraft({ ...draft, name: v })} />
          </Field>
          <Field label="Narx">
            <TextInput
              value={draft.price}
              onChange={(v) => setDraft({ ...draft, price: v })}
              type="number"
              mono
            />
          </Field>
          <Field label="Muddat (kun)">
            <TextInput
              value={draft.duration_days}
              onChange={(v) => setDraft({ ...draft, duration_days: v })}
              type="number"
              mono
            />
          </Field>
        </div>
        <Button onClick={addPlan} disabled={busy || !draft.code || !draft.name}>
          <Plus size={14} /> Tarif qo'shish
        </Button>
      </div>

      {plans.length === 0 ? (
        <EmptyState message="Tarif yo'q." />
      ) : (
        <ul className="space-y-2">
          {plans.map((plan, index) => (
            <li key={plan.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-start justify-between gap-2">
                <button
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setExpanded(expanded === plan.id ? null : plan.id)}
                >
                  <p className="truncate font-body text-sm text-ink">
                    {plan.name}
                    {plan.is_free && <span className="ml-1.5 text-xs text-ink-dim">(bepul)</span>}
                    {!plan.is_active && <span className="ml-1.5 text-xs text-ink-dim">— o'chiq</span>}
                  </p>
                  <p className="font-mono text-[11px] text-ink-dim">
                    {plan.code} · {formatMoney(plan.price)} · {plan.duration_days} kun ·{" "}
                    {plan.subscriber_count} obunachi
                  </p>
                </button>
                <div className="flex shrink-0 gap-1">
                  <Button tone="ghost" disabled={busy || index === 0} onClick={() => move(index, -1)}>
                    <ArrowUp size={13} />
                  </Button>
                  <Button
                    tone="ghost"
                    disabled={busy || index === plans.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    <ArrowDown size={13} />
                  </Button>
                  <Button tone="ghost" disabled={busy} onClick={() => void guard(() => adminApi.togglePlan(plan.id))}>
                    {plan.is_active ? "Off" : "On"}
                  </Button>
                  <Button
                    tone="danger"
                    // The API refuses this too; disabling explains why first.
                    disabled={busy || plan.subscriber_count > 0}
                    title={plan.subscriber_count > 0 ? "Obunachilari bor — o'chirib bo'lmaydi" : undefined}
                    onClick={() => void guard(() => adminApi.deletePlan(plan.id))}
                  >
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>

              {expanded === plan.id && (
                <div className="mt-3 space-y-3 border-t border-surface-hi pt-3">
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Narx">
                      <TextInput
                        value={String(plan.price)}
                        onChange={(v) =>
                          void guard(() => adminApi.updatePlan(plan.id, { price: Number(v || 0) }))
                        }
                        type="number"
                        mono
                      />
                    </Field>
                    <Field label="Muddat (kun)">
                      <TextInput
                        value={String(plan.duration_days)}
                        onChange={(v) =>
                          void guard(() =>
                            adminApi.updatePlan(plan.id, { duration_days: Number(v || 1) }),
                          )
                        }
                        type="number"
                        mono
                      />
                    </Field>
                  </div>

                  <Field label="Afzalliklar (har qatorda bittadan)">
                    <TextArea
                      value={plan.benefits.join("\n")}
                      onChange={(v) =>
                        void guard(() =>
                          adminApi.updatePlan(plan.id, {
                            benefits: v.split("\n").map((line) => line.trim()).filter(Boolean),
                          }),
                        )
                      }
                      placeholder="Cheksiz AI tavsiyalar"
                    />
                  </Field>

                  <div>
                    <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-ink-dim">
                      Imkoniyatlar
                    </p>
                    {features.length === 0 ? (
                      <p className="font-body text-xs text-ink-dim">
                        Imkoniyat yo'q — quyida qo'shing.
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {features.map((feature) => {
                          const active = plan.features.some((f) => f.id === feature.id);
                          return (
                            <button
                              key={feature.id}
                              disabled={busy}
                              onClick={() => toggleFeature(plan, feature)}
                              className={`flex items-center gap-1 rounded-full px-2.5 py-1 font-body text-[11px] transition-colors disabled:opacity-50 ${
                                active
                                  ? "bg-marquee text-on-marquee"
                                  : "border border-surface-hi bg-bg text-ink-dim"
                              }`}
                            >
                              {active && <Check size={10} />}
                              {feature.name}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <SectionTitle>Imkoniyatlar</SectionTitle>
      <div className="space-y-2 rounded-xl border border-surface-hi bg-surface p-3">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Kod">
            <TextInput
              value={featureDraft.code}
              onChange={(v) => setFeatureDraft({ ...featureDraft, code: v })}
              mono
            />
          </Field>
          <Field label="Nomi">
            <TextInput
              value={featureDraft.name}
              onChange={(v) => setFeatureDraft({ ...featureDraft, name: v })}
            />
          </Field>
        </div>
        <Button
          disabled={busy || !featureDraft.code || !featureDraft.name}
          onClick={() =>
            void guard(async () => {
              await adminApi.createSubscriptionFeature({
                code: featureDraft.code.trim(),
                name: featureDraft.name.trim(),
              });
              setFeatureDraft({ code: "", name: "" });
            })
          }
        >
          <Plus size={14} /> Imkoniyat qo'shish
        </Button>

        {features.length > 0 && (
          <ul className="space-y-1 pt-1">
            {features.map((feature) => (
              <li key={feature.id} className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate font-body text-sm text-ink">
                  {feature.name}{" "}
                  <span className="font-mono text-[11px] text-ink-dim">{feature.code}</span>
                </span>
                <Button
                  tone="danger"
                  disabled={busy}
                  onClick={() => void guard(() => adminApi.deleteSubscriptionFeature(feature.id))}
                >
                  <Trash2 size={13} />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
