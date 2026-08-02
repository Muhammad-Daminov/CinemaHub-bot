/**
 * Videos the bot received from an admin but that aren't attached to an
 * episode yet. Attaching either targets an existing title (found by
 * search) or creates a new one inline, so a forwarded file can become
 * catalog content without leaving the phone.
 */
import { ArrowLeft, FileVideo, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type {
  AdminTitleListItem,
  AudioLanguage,
  ContentType,
  PendingUpload,
  VideoQuality,
} from "../types/admin";
import {
  AUDIO_LANGUAGES,
  Badge,
  Button,
  CONTENT_TYPES,
  CardShell,
  EmptyState,
  Field,
  IconButton,
  Notice,
  SectionTitle,
  Select,
  TextInput,
  VIDEO_QUALITIES,
  formatBytes,
  formatDuration,
} from "./ui";

type Mode = "existing" | "new";

function AttachForm({ upload, onDone }: { upload: PendingUpload; onDone: () => void }) {
  const [mode, setMode] = useState<Mode>("existing");
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<AdminTitleListItem[]>([]);
  const [selected, setSelected] = useState<AdminTitleListItem | null>(null);
  const [newName, setNewName] = useState(upload.file_name ?? "");
  const [newType, setNewType] = useState<ContentType>("film");
  const [newYear, setNewYear] = useState("");
  const [season, setSeason] = useState("1");
  const [number, setNumber] = useState("1");
  const [language, setLanguage] = useState<AudioLanguage>("uz_dub");
  const [quality, setQuality] = useState<VideoQuality>("720p");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "existing" || search.trim().length < 2) {
      setResults([]);
      return;
    }
    adminApi
      .listTitles({ q: search.trim(), page_size: 10 })
      .then((page) => setResults(page.items))
      .catch(() => setResults([]));
  }, [search, mode]);

  const handleSubmit = async () => {
    if (mode === "existing" && selected == null) {
      setError("Kontent tanlanmadi.");
      return;
    }
    if (mode === "new" && !newName.trim()) {
      setError("Yangi kontent nomi kiritilmadi.");
      return;
    }

    try {
      await adminApi.attachPendingUpload(upload.id, {
        title_id: mode === "existing" ? selected?.id : undefined,
        name: mode === "new" ? newName.trim() : undefined,
        content_type: mode === "new" ? newType : undefined,
        year: mode === "new" && newYear.trim() ? Number(newYear) : undefined,
        season: Number(season) || 1,
        number: Number(number) || 1,
        language,
        quality,
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Biriktirishda xatolik.");
    }
  };

  return (
    <div className="space-y-4">
      <button
        onClick={onDone}
        className="inline-flex items-center gap-1.5 font-body text-sm text-ink-dim"
      >
        <ArrowLeft size={16} /> Orqaga
      </button>

      <CardShell>
        <p className="truncate font-body text-sm text-ink">{upload.file_name ?? "Nomsiz fayl"}</p>
        <p className="mt-1 font-mono text-[11px] text-ink-dim">
          {formatBytes(upload.file_size)} · {formatDuration(upload.duration_seconds)}
        </p>
      </CardShell>

      <div className="grid grid-cols-2 gap-2">
        <Button tone={mode === "existing" ? "primary" : "ghost"} onClick={() => setMode("existing")}>
          Mavjud kontent
        </Button>
        <Button tone={mode === "new" ? "primary" : "ghost"} onClick={() => setMode("new")}>
          Yangi kontent
        </Button>
      </div>

      {mode === "existing" ? (
        <div className="space-y-2">
          <Field label="Kontentni qidirish">
            <TextInput value={search} onChange={setSearch} placeholder="Nomi…" />
          </Field>
          {selected && (
            <Notice message={`Tanlandi: ${selected.name}`} />
          )}
          <ul className="space-y-1">
            {results.map((item) => (
              <li key={item.id}>
                <button
                  onClick={() => setSelected(item)}
                  className={`w-full rounded-lg border px-3 py-2 text-left font-body text-sm ${
                    selected?.id === item.id
                      ? "border-marquee bg-surface-hi text-ink"
                      : "border-surface-hi bg-surface text-ink-dim"
                  }`}
                >
                  {item.name}
                  {item.year != null && <span className="font-mono text-[11px]"> · {item.year}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="space-y-2">
          <Field label="Nomi">
            <TextInput value={newName} onChange={setNewName} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Turi">
              <Select<ContentType> value={newType} onChange={setNewType} options={CONTENT_TYPES} />
            </Field>
            <Field label="Yil">
              <TextInput value={newYear} onChange={setNewYear} type="number" />
            </Field>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <Field label="Fasl">
          <TextInput value={season} onChange={setSeason} type="number" />
        </Field>
        <Field label="Qism raqami">
          <TextInput value={number} onChange={setNumber} type="number" />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Til">
          <Select<AudioLanguage> value={language} onChange={setLanguage} options={AUDIO_LANGUAGES} />
        </Field>
        <Field label="Sifat">
          <Select<VideoQuality> value={quality} onChange={setQuality} options={VIDEO_QUALITIES} />
        </Field>
      </div>

      <Button full onClick={handleSubmit}>
        Biriktirish
      </Button>
      {error && <Notice message={error} tone="error" />}
    </div>
  );
}

export function UploadsPanel() {
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [active, setActive] = useState<PendingUpload | null>(null);

  const load = useCallback(async () => {
    try {
      setUploads(await adminApi.pendingUploads());
    } catch {
      setUploads([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDelete = async (id: number) => {
    if (!window.confirm("Bu yuklama o'chirilsinmi?")) return;
    await adminApi.deletePendingUpload(id).catch(() => undefined);
    load();
  };

  if (active) {
    return (
      <AttachForm
        upload={active}
        onDone={() => {
          setActive(null);
          load();
        }}
      />
    );
  }

  return (
    <div className="space-y-3">
      <SectionTitle>Kutayotgan yuklamalar</SectionTitle>
      {uploads.length === 0 ? (
        <EmptyState message="Yangi yuklama yo'q. Botga video yuboring." />
      ) : (
        <ul className="space-y-2">
          {uploads.map((upload) => (
            <li key={upload.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <button onClick={() => setActive(upload)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  <FileVideo size={18} className="shrink-0 text-marquee" />
                  <div className="min-w-0">
                    <p className="truncate font-body text-sm text-ink">
                      {upload.file_name ?? "Nomsiz fayl"}
                    </p>
                    <p className="font-mono text-[11px] text-ink-dim">
                      {formatBytes(upload.file_size)} · {formatDuration(upload.duration_seconds)}
                    </p>
                  </div>
                </button>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Badge>Yangi</Badge>
                  <IconButton label="O'chirish" tone="danger" onClick={() => handleDelete(upload.id)}>
                    <Trash2 size={15} />
                  </IconButton>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
