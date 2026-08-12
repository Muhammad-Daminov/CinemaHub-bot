/**
 * Catalog browser. Stacked cards rather than a table — a 7-column table
 * cannot fit a phone without horizontal scroll, which the Mini App must
 * not have.
 */
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminTitleListItem, ContentType } from "../types/admin";
import { TitleEditor } from "./TitleEditor";
import {
  Badge,
  Button,
  CONTENT_TYPES,
  EmptyState,
  IconButton,
  Notice,
  Select,
  TextInput,
  contentTypeLabel,
} from "./ui";

type TypeFilter = ContentType | "all";
type ActiveFilter = "all" | "active" | "hidden";
type PremiumFilter = "all" | "premium" | "free";

const PAGE_SIZE = 20;

export function ContentPanel() {
  const [items, setItems] = useState<AdminTitleListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("all");
  const [premiumFilter, setPremiumFilter] = useState<PremiumFilter>("all");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await adminApi.listTitles({
        q: query || undefined,
        content_type: typeFilter === "all" ? undefined : typeFilter,
        is_active: activeFilter === "all" ? undefined : activeFilter === "active",
        // Omitted for "all". Filtered by the server so the result count and
        // the pages agree with what is on screen — dropping rows here would
        // leave `total` describing a different set.
        is_premium: premiumFilter === "all" ? undefined : premiumFilter === "premium",
        page,
        page_size: PAGE_SIZE,
      });
      setItems(result.items);
      setTotal(result.total);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklashda xatolik.");
    }
  }, [query, typeFilter, activeFilter, premiumFilter, page]);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = async (id: number) => {
    await adminApi.toggleTitle(id).catch(() => undefined);
    load();
  };

  const handleDelete = async (item: AdminTitleListItem) => {
    if (!window.confirm(`"${item.name}" o'chirilsinmi? Qismlari ham o'chadi.`)) return;
    // The failure is shown rather than swallowed. This previously ended in
    // `.catch(() => undefined)` and then reloaded the list, so when the
    // delete 500'd the panel looked like it had worked and simply put the
    // title back — which is how a server-side delete bug stayed invisible.
    // A refusal an operator cannot see is worse than a visible error.
    try {
      await adminApi.deleteTitle(item.id);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "O'chirib bo'lmadi.");
    }
    load();
  };

  if (creating || editingId !== null) {
    return (
      <TitleEditor
        titleId={editingId}
        onClose={() => {
          setCreating(false);
          setEditingId(null);
          load();
        }}
        onOpenTitle={(id) => {
          // Tapped a duplicate match — edit that one instead of creating another.
          setCreating(false);
          setEditingId(id);
        }}
      />
    );
  }

  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <TextInput
          value={query}
          onChange={(value) => {
            setPage(0);
            setQuery(value);
          }}
          placeholder="Nomi bo'yicha qidirish…"
        />
        <div className="grid grid-cols-2 gap-2">
          <Select<TypeFilter>
            value={typeFilter}
            onChange={(value) => {
              setPage(0);
              setTypeFilter(value);
            }}
            options={[{ value: "all", label: "Barcha turlar" }, ...CONTENT_TYPES]}
          />
          <Select<ActiveFilter>
            value={activeFilter}
            onChange={(value) => {
              setPage(0);
              setActiveFilter(value);
            }}
            options={[
              { value: "all", label: "Barchasi" },
              { value: "active", label: "Faol" },
              { value: "hidden", label: "Yashirin" },
            ]}
          />
          <Select<PremiumFilter>
            value={premiumFilter}
            onChange={(value) => {
              setPage(0);
              setPremiumFilter(value);
            }}
            options={[
              { value: "all", label: "Premium: barchasi" },
              { value: "premium", label: "Faqat premium" },
              { value: "free", label: "Faqat oddiy" },
            ]}
          />
        </div>
        <Button full onClick={() => setCreating(true)}>
          <span className="inline-flex items-center justify-center gap-1.5">
            <Plus size={15} /> Yangi kontent
          </span>
        </Button>
      </div>

      {error && <Notice message={error} tone="error" />}

      <p className="font-mono text-[11px] text-ink-dim">{total} ta natija</p>

      {items.length === 0 ? (
        <EmptyState message="Hech narsa topilmadi." />
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-display text-sm font-medium text-ink">{item.name}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge>{contentTypeLabel(item.content_type)}</Badge>
                    {item.year != null && (
                      <span className="font-mono text-[11px] text-ink-dim">{item.year}</span>
                    )}
                    <Badge active={item.is_active}>{item.is_active ? "Faol" : "Yashirin"}</Badge>
                    {item.is_premium && <Badge active>Premium</Badge>}
                  </div>
                  <p className="mt-1 font-mono text-[11px] text-ink-dim">
                    {item.code && <>Kod {item.code} · </>}
                    {item.episode_count} qism · {item.file_count} fayl
                  </p>
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <IconButton label="Faollik" onClick={() => handleToggle(item.id)}>
                    <Power size={15} />
                  </IconButton>
                  <IconButton label="Tahrirlash" onClick={() => setEditingId(item.id)}>
                    <Pencil size={15} />
                  </IconButton>
                  <IconButton label="O'chirish" tone="danger" onClick={() => handleDelete(item)}>
                    <Trash2 size={15} />
                  </IconButton>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {lastPage > 0 && (
        <div className="flex items-center justify-between pt-2">
          <Button tone="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Oldingi
          </Button>
          <span className="font-mono text-xs text-ink-dim">
            {page + 1} / {lastPage + 1}
          </span>
          <Button tone="ghost" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
            Keyingi
          </Button>
        </div>
      )}
    </div>
  );
}
