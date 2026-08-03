/**
 * Curated collections — "Marvel", "Yangi yil kinolari".
 *
 * A title belongs to many collections at once, so membership is edited
 * from two directions: here (which titles are in this collection) and
 * from TitleEditor (which collections this title is in). Both call the
 * same endpoints.
 */
import { ArrowLeft, Pencil, Plus, Power, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type { AdminCollectionListItem, AdminTitle, AdminTitleListItem } from "../types/admin";
import {
  Badge,
  Button,
  CardShell,
  EmptyState,
  Field,
  IconButton,
  Notice,
  SectionTitle,
  TextArea,
  TextInput,
  contentTypeLabel,
} from "./ui";

interface FormState {
  name: string;
  description: string;
  poster_url: string;
  sort_order: string;
}

const EMPTY_FORM: FormState = { name: "", description: "", poster_url: "", sort_order: "0" };

/** Manage which titles sit inside one collection. */
function CollectionTitles({
  collection,
  onClose,
}: {
  collection: AdminCollectionListItem;
  onClose: () => void;
}) {
  const [titles, setTitles] = useState<AdminTitle[]>([]);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<AdminTitleListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTitles(await adminApi.collectionTitles(collection.id));
    } catch {
      setTitles([]);
    }
  }, [collection.id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (search.trim().length < 2) {
      setResults([]);
      return;
    }
    adminApi
      .listTitles({ q: search.trim(), page_size: 10 })
      .then((page) => setResults(page.items))
      .catch(() => setResults([]));
  }, [search]);

  const add = async (titleId: number) => {
    try {
      await adminApi.addTitleToCollection(collection.id, titleId);
      setSearch("");
      setResults([]);
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Qo'shishda xatolik.");
    }
  };

  const remove = async (titleId: number) => {
    await adminApi.removeTitleFromCollection(collection.id, titleId).catch(() => undefined);
    load();
  };

  const memberIds = new Set(titles.map((t) => t.id));

  return (
    <div className="space-y-4">
      <button onClick={onClose} className="inline-flex items-center gap-1.5 font-body text-sm text-ink-dim">
        <ArrowLeft size={16} /> Orqaga
      </button>

      <SectionTitle>{collection.name}</SectionTitle>

      <CardShell>
        <Field label="Kontent qidirish va qo'shish">
          <TextInput value={search} onChange={setSearch} placeholder="Nomi…" />
        </Field>
        {results.length > 0 && (
          <ul className="mt-2 space-y-1">
            {results.map((item) => (
              <li key={item.id}>
                <button
                  onClick={() => add(item.id)}
                  disabled={memberIds.has(item.id)}
                  className="w-full rounded-lg border border-surface-hi bg-surface px-3 py-2 text-left font-body text-sm text-ink disabled:opacity-40"
                >
                  {item.name}
                  {item.year != null && <span className="font-mono text-[11px]"> · {item.year}</span>}
                  {memberIds.has(item.id) && <span className="text-ink-dim"> — allaqachon</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
        {error && <div className="mt-2"><Notice message={error} tone="error" /></div>}
      </CardShell>

      {titles.length === 0 ? (
        <EmptyState message="Bu to'plamda kontent yo'q." />
      ) : (
        <ul className="space-y-2">
          {titles.map((title) => (
            <li
              key={title.id}
              className="flex items-center justify-between gap-2 rounded-xl border border-surface-hi bg-surface p-3"
            >
              <div className="min-w-0">
                <p className="truncate font-body text-sm text-ink">{title.name}</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge>{contentTypeLabel(title.content_type)}</Badge>
                  {title.year != null && (
                    <span className="font-mono text-[11px] text-ink-dim">{title.year}</span>
                  )}
                </div>
              </div>
              <IconButton label="Chiqarish" tone="danger" onClick={() => remove(title.id)}>
                <X size={15} />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CollectionsPanel() {
  const [collections, setCollections] = useState<AdminCollectionListItem[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [managing, setManaging] = useState<AdminCollectionListItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCollections(await adminApi.listCollections());
    } catch {
      setCollections([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setEditingId(null);
  };

  const submit = async () => {
    if (!form.name.trim()) {
      setError("Nom kiritilishi shart.");
      return;
    }
    const payload = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      poster_url: form.poster_url.trim() || null,
      sort_order: Number(form.sort_order) || 0,
    };
    try {
      if (editingId == null) {
        await adminApi.createCollection(payload);
      } else {
        await adminApi.updateCollection(editingId, payload);
      }
      resetForm();
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saqlashda xatolik.");
    }
  };

  const startEdit = (collection: AdminCollectionListItem) => {
    setEditingId(collection.id);
    setForm({
      name: collection.name,
      description: collection.description ?? "",
      poster_url: collection.poster_url ?? "",
      sort_order: String(collection.sort_order),
    });
  };

  const toggle = async (id: number) => {
    await adminApi.toggleCollection(id).catch(() => undefined);
    load();
  };

  const remove = async (collection: AdminCollectionListItem) => {
    if (!window.confirm(`"${collection.name}" to'plami o'chirilsinmi? Kontentning o'zi qoladi.`)) return;
    await adminApi.deleteCollection(collection.id).catch(() => undefined);
    load();
  };

  if (managing) {
    return (
      <CollectionTitles
        collection={managing}
        onClose={() => {
          setManaging(null);
          load();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <section>
        <SectionTitle>To'plamlar</SectionTitle>
        {collections.length === 0 ? (
          <EmptyState message="To'plam yo'q." />
        ) : (
          <ul className="space-y-2">
            {collections.map((collection) => (
              <li key={collection.id} className="rounded-xl border border-surface-hi bg-surface p-3">
                <div className="flex items-start justify-between gap-2">
                  <button
                    onClick={() => setManaging(collection)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <p className="truncate font-display text-sm font-medium text-ink">
                      {collection.name}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Badge active={collection.is_active}>
                        {collection.is_active ? "Faol" : "Yashirin"}
                      </Badge>
                      <span className="font-mono text-[11px] text-ink-dim">
                        {collection.title_count} ta · #{collection.sort_order}
                      </span>
                    </div>
                  </button>
                  <div className="flex shrink-0 gap-1.5">
                    <IconButton label="Faollik" onClick={() => toggle(collection.id)}>
                      <Power size={15} />
                    </IconButton>
                    <IconButton label="Tahrirlash" onClick={() => startEdit(collection)}>
                      <Pencil size={15} />
                    </IconButton>
                    <IconButton label="O'chirish" tone="danger" onClick={() => remove(collection)}>
                      <Trash2 size={15} />
                    </IconButton>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>{editingId == null ? "Yangi to'plam" : "To'plamni tahrirlash"}</SectionTitle>
        <CardShell>
          <div className="space-y-2">
            <Field label="Nomi">
              <TextInput value={form.name} onChange={(v) => update("name", v)} placeholder="Marvel" />
            </Field>
            <Field label="Tavsif">
              <TextArea
                value={form.description}
                onChange={(v) => update("description", v)}
                rows={2}
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Poster havolasi">
                <TextInput value={form.poster_url} onChange={(v) => update("poster_url", v)} mono />
              </Field>
              <Field label="Tartib raqami">
                <TextInput
                  value={form.sort_order}
                  onChange={(v) => update("sort_order", v)}
                  type="number"
                />
              </Field>
            </div>
            <div className="flex gap-2">
              <Button full onClick={submit}>
                <span className="inline-flex items-center justify-center gap-1.5">
                  <Plus size={15} /> {editingId == null ? "Yaratish" : "Saqlash"}
                </span>
              </Button>
              {editingId != null && (
                <Button tone="ghost" onClick={resetForm}>
                  Bekor
                </Button>
              )}
            </div>
            {error && <Notice message={error} tone="error" />}
          </div>
        </CardShell>
      </section>
    </div>
  );
}
