/**
 * Create/edit one title, plus its episode and media-file manager.
 *
 * Episodes are grouped by season because a serial with six seasons is
 * unreadable as one flat list on a phone.
 *
 * Per-episode file listing degrades gracefully: the backend currently
 * exposes POST /admin/episodes/{id}/files and DELETE /admin/files/{id}
 * but no GET, so on failure we fall back to the episode's file_count and
 * show what was attached in this session.
 */
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Languages,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import { PosterPicker } from "./PosterPicker";
import type {
  AdminTitleTranslation,
  TranslationLanguage,
  TranslationSource,
  AdminCollectionListItem,
  AdminEpisode,
  AdminMediaFile,
  AdminTitle,
  AudioLanguage,
  ContentType,
  SimilarTitle,
  TMDBSearchResult,
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
  TextArea,
  TextInput,
  VIDEO_QUALITIES,
  contentTypeLabel,
  languageLabel,
} from "./ui";

interface Props {
  titleId: number | null;
  onClose: () => void;
  /** Jump to an existing title — used when a duplicate match is tapped. */
  onOpenTitle: (id: number) => void;
}

interface FormState {
  name: string;
  content_type: ContentType;
  year: string;
  country: string;
  genres: string;
  description: string;
  poster_url: string;
  rating: string;
}

// The stored Title.name is the Uzbek name and the fallback for every
// language, so a `uz` row is an override rather than the norm — it is
// offered because a title can legitimately read differently in Uzbek
// than the name the catalog is indexed by.
const TRANSLATION_LANGUAGES: { code: TranslationLanguage; label: string }[] = [
  { code: "uz", label: "O'zbekcha" },
  { code: "ru", label: "Ruscha" },
  { code: "en", label: "Inglizcha" },
];

const EMPTY_FORM: FormState = {
  name: "",
  content_type: "film",
  year: "",
  country: "",
  genres: "",
  description: "",
  poster_url: "",
  rating: "",
};

function toForm(title: AdminTitle): FormState {
  return {
    name: title.name,
    content_type: title.content_type,
    year: title.year?.toString() ?? "",
    country: title.country ?? "",
    genres: (title.genres ?? []).join(", "),
    description: title.description ?? "",
    poster_url: title.poster_url ?? "",
    rating: title.rating?.toString() ?? "",
  };
}

const parseNumber = (value: string): number | null => {
  const parsed = Number(value.trim());
  return value.trim() === "" || Number.isNaN(parsed) ? null : parsed;
};

const parseGenres = (value: string): string[] | null => {
  const list = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return list.length > 0 ? list : null;
};

/**
 * Possible duplicates for the name being typed.
 *
 * The language badges are the real payload: the admin needs to see not
 * just "this exists" but "the Russian dub is already on it", which is
 * the thing they were about to add it for.
 */
function DuplicateWarning({
  matches,
  onOpen,
}: {
  matches: SimilarTitle[];
  onOpen: (id: number) => void;
}) {
  if (matches.length === 0) return null;

  return (
    <div className="rounded-xl border border-premiere bg-surface p-3">
      <p className="mb-2 font-body text-xs font-medium text-premiere">
        ⚠️ Shunga o'xshash kontent allaqachon bor — yangisini yaratmang:
      </p>
      <ul className="space-y-1.5">
        {matches.map((match) => (
          <li key={match.id}>
            <button
              onClick={() => onOpen(match.id)}
              className="flex w-full items-center gap-2 rounded-lg bg-surface-hi p-2 text-left"
            >
              <div className="h-12 w-8 shrink-0 overflow-hidden rounded bg-surface">
                {match.poster_url && (
                  <img
                    src={match.poster_url}
                    alt=""
                    className="h-full w-full object-cover"
                    loading="lazy"
                  />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate font-body text-sm text-ink">{match.name}</p>
                <p className="font-mono text-[10px] text-ink-dim">
                  {contentTypeLabel(match.content_type)}
                  {match.year != null ? ` · ${match.year}` : ""} · {match.episode_count} qism
                </p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {match.languages.length === 0 ? (
                    <span className="font-mono text-[10px] text-ink-dim">fayl yo'q</span>
                  ) : (
                    match.languages.map((language) => (
                      <Badge key={language}>{languageLabel(language)}</Badge>
                    ))
                  )}
                </div>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Manual TMDB picker.
 *
 * Auto-enrich searches on the stored name, which is Uzbek ("Qum
 * sayyorasi") while TMDB indexes English ("Dune") — so it misses most of
 * this catalog. Here the admin types the English name and picks the right
 * record. Applying it never touches the Uzbek name.
 */
function TMDBSearchBox({
  titleId,
  onApplied,
}: {
  titleId: number;
  onApplied: (title: AdminTitle) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TMDBSearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (query.trim().length < 2) return;
    setBusy(true);
    try {
      setResults(await adminApi.searchTmdb(query.trim()));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "TMDB qidiruvida xatolik.");
    } finally {
      setBusy(false);
    }
  };

  const apply = async (tmdbId: number) => {
    try {
      const updated = await adminApi.applyTmdbMatch(titleId, tmdbId);
      setResults([]);
      setQuery("");
      setOpen(false);
      setError(null);
      onApplied(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Qo'llashda xatolik.");
    }
  };

  if (!open) {
    return (
      <Button tone="ghost" onClick={() => setOpen(true)}>
        <span className="inline-flex items-center gap-1.5">
          <Search size={15} /> TMDB'da qidirish
        </span>
      </Button>
    );
  }

  return (
    <CardShell>
      <Field label="TMDB'da inglizcha nomi bilan qidiring">
        <TextInput
          value={query}
          onChange={setQuery}
          placeholder="Masalan: Dune"
        />
      </Field>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Button onClick={search} disabled={busy || query.trim().length < 2}>
          {busy ? "Qidirilmoqda…" : "Qidirish"}
        </Button>
        <Button
          tone="ghost"
          onClick={() => {
            setOpen(false);
            setResults([]);
            setError(null);
          }}
        >
          Yopish
        </Button>
      </div>

      {results.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {results.map((result) => (
            <li key={result.id}>
              <button
                onClick={() => apply(result.id)}
                className="flex w-full items-center gap-2 rounded-lg bg-surface-hi p-2 text-left"
              >
                <div className="h-14 w-10 shrink-0 overflow-hidden rounded bg-surface">
                  {result.poster_url && (
                    <img
                      src={result.poster_url}
                      alt=""
                      loading="lazy"
                      className="h-full w-full object-cover"
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-body text-sm text-ink">{result.title}</p>
                  {result.original_title && result.original_title !== result.title && (
                    <p className="truncate font-body text-[11px] text-ink-dim">
                      {result.original_title}
                    </p>
                  )}
                  <p className="font-mono text-[10px] text-ink-dim">
                    {result.year ?? "—"} · TMDB #{result.id}
                  </p>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <div className="mt-2"><Notice message={error} tone="error" /></div>}
    </CardShell>
  );
}


/** Multi-select over collections; membership is saved immediately on toggle. */
function CollectionPicker({ titleId }: { titleId: number }) {
  const [collections, setCollections] = useState<AdminCollectionListItem[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.listCollections().then(setCollections).catch(() => setCollections([]));
    adminApi.titleCollections(titleId).then(setSelected).catch(() => setSelected([]));
  }, [titleId]);

  const toggle = async (collectionId: number) => {
    const next = selected.includes(collectionId)
      ? selected.filter((id) => id !== collectionId)
      : [...selected, collectionId];
    setSelected(next);
    try {
      setSelected(await adminApi.setTitleCollections(titleId, next));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "To'plamni saqlashda xatolik.");
    }
  };

  if (collections.length === 0) return null;

  return (
    <section>
      <SectionTitle>To'plamlar</SectionTitle>
      <div className="flex flex-wrap gap-1.5">
        {collections.map((collection) => {
          const active = selected.includes(collection.id);
          return (
            <button
              key={collection.id}
              onClick={() => toggle(collection.id)}
              className={`rounded-full px-3 py-1.5 font-body text-xs transition-colors ${
                active
                  ? "bg-marquee text-on-marquee"
                  : "border border-surface-hi bg-surface text-ink-dim"
              }`}
            >
              {collection.name}
            </button>
          );
        })}
      </div>
      {error && <div className="mt-2"><Notice message={error} tone="error" /></div>}
    </section>
  );
}


/**
 * Per-language name and description for one title.
 *
 * The stored name is deliberately not shown as an editable "Uzbek" row —
 * it is the title's own name field above, and the fallback for every
 * language. What is edited here are the overrides.
 *
 * Emptying a name and saving removes that language's translation, which
 * is the only way a form of plain text fields can express "delete this".
 * TMDB'dan olish fills Russian and English from TMDB without touching
 * anything an administrator typed.
 */
function TranslationEditor({ titleId, hasTmdbId }: { titleId: number; hasTmdbId: boolean }) {
  const [rows, setRows] = useState<Record<string, { name: string; description: string }>>({});
  const [sources, setSources] = useState<Record<string, TranslationSource>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const absorb = useCallback((list: AdminTitleTranslation[]) => {
    const next: Record<string, { name: string; description: string }> = {};
    const nextSources: Record<string, TranslationSource> = {};
    for (const item of list) {
      next[item.language] = { name: item.name, description: item.description ?? "" };
      nextSources[item.language] = item.source;
    }
    setRows(next);
    setSources(nextSources);
  }, []);

  useEffect(() => {
    adminApi.titleTranslations(titleId).then(absorb).catch(() => undefined);
  }, [titleId, absorb]);

  const update = (language: string, field: "name" | "description", value: string) =>
    setRows((current) => ({
      ...current,
      // Written out rather than spread-plus-override: a spread after the
      // defaults would put the stored value back over the character just
      // typed, and TypeScript flags that shape for exactly this reason.
      [language]: {
        name: field === "name" ? value : (current[language]?.name ?? ""),
        description: field === "description" ? value : (current[language]?.description ?? ""),
      },
    }));

  const save = async () => {
    setBusy(true);
    try {
      absorb(
        await adminApi.setTitleTranslations(
          titleId,
          TRANSLATION_LANGUAGES.map(({ code }) => ({
            language: code,
            name: rows[code]?.name ?? "",
            description: rows[code]?.description || null,
          })),
        ),
      );
      setError(null);
      setMessage("Tarjimalar saqlandi.");
    } catch (err) {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "Tarjimani saqlashda xatolik.");
    } finally {
      setBusy(false);
    }
  };

  const fillFromTmdb = async () => {
    setBusy(true);
    try {
      absorb(await adminApi.fillTitleTranslations(titleId));
      setError(null);
      setMessage("TMDB tarjimalari olindi.");
    } catch (err) {
      setMessage(null);
      setError(err instanceof ApiError ? err.message : "TMDB tarjimasi olinmadi.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <SectionTitle>Tarjimalar</SectionTitle>
      <p className="mb-2 font-body text-xs text-ink-dim">
        Foydalanuvchi tanlagan tilda ko'radi. Bo'sh qoldirilsa yuqoridagi asosiy nom ishlatiladi.
      </p>

      <div className="space-y-3">
        {TRANSLATION_LANGUAGES.map(({ code, label }) => (
          <div key={code} className="rounded-xl border border-surface-hi bg-surface p-3">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="font-body text-xs font-medium text-ink">{label}</span>
              {sources[code] === "tmdb" && <Badge>TMDB</Badge>}
            </div>
            <div className="space-y-2">
              <TextInput
                value={rows[code]?.name ?? ""}
                onChange={(value) => update(code, "name", value)}
                placeholder="Nom"
              />
              <TextArea
                value={rows[code]?.description ?? ""}
                onChange={(value) => update(code, "description", value)}
                placeholder="Tavsif"
                rows={2}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        <Button disabled={busy} onClick={save}>
          Tarjimalarni saqlash
        </Button>
        <Button
          tone="ghost"
          disabled={busy || !hasTmdbId}
          title={hasTmdbId ? undefined : "Avval TMDB mosligini tanlang"}
          onClick={fillFromTmdb}
        >
          <span className="inline-flex items-center gap-1.5">
            <Languages size={15} /> TMDB'dan olish
          </span>
        </Button>
      </div>

      {message && <div className="mt-2"><Notice message={message} /></div>}
      {error && <div className="mt-2"><Notice message={error} tone="error" /></div>}
    </section>
  );
}


function EpisodeFiles({ episode }: { episode: AdminEpisode }) {
  const [files, setFiles] = useState<AdminMediaFile[]>([]);
  const [listable, setListable] = useState(true);
  const [fileId, setFileId] = useState("");
  const [language, setLanguage] = useState<AudioLanguage>("uz_dub");
  const [quality, setQuality] = useState<VideoQuality>("720p");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setFiles(await adminApi.listEpisodeFiles(episode.id));
      setListable(true);
    } catch {
      // No GET route on the backend yet — keep the attach flow usable.
      setListable(false);
    }
  }, [episode.id]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAttach = async () => {
    if (!fileId.trim()) return;
    try {
      const created = await adminApi.attachFile(episode.id, {
        file_id: fileId.trim(),
        language,
        quality,
      });
      setFiles((current) => [...current.filter((item) => item.id !== created.id), created]);
      setFileId("");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Biriktirishda xatolik.");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await adminApi.deleteFile(id);
      setFiles((current) => current.filter((item) => item.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "O'chirishda xatolik.");
    }
  };

  return (
    <div className="mt-2 space-y-2 border-t border-surface-hi pt-2">
      {!listable && (
        <Notice
          message={`Bu qismda ${episode.file_count} ta fayl bor. Ro'yxatni ko'rsatish uchun backendda GET /admin/episodes/{id}/files yo'q.`}
        />
      )}

      {files.length > 0 && (
        <ul className="space-y-1">
          {files.map((file) => (
            <li
              key={file.id}
              className="flex items-center justify-between gap-2 rounded-lg bg-surface-hi px-2 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate font-body text-xs text-ink">
                  {languageLabel(file.language)} · {file.quality}
                </p>
                <p className="truncate font-mono text-[10px] text-ink-dim">{file.file_id}</p>
              </div>
              <IconButton label="Faylni o'chirish" tone="danger" onClick={() => handleDelete(file.id)}>
                <Trash2 size={13} />
              </IconButton>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-2">
        <TextInput value={fileId} onChange={setFileId} placeholder="Telegram file_id" mono />
        <div className="grid grid-cols-2 gap-2">
          <Select<AudioLanguage> value={language} onChange={setLanguage} options={AUDIO_LANGUAGES} />
          <Select<VideoQuality> value={quality} onChange={setQuality} options={VIDEO_QUALITIES} />
        </div>
        <Button full tone="ghost" onClick={handleAttach} disabled={!fileId.trim()}>
          Fayl biriktirish
        </Button>
      </div>

      {error && <Notice message={error} tone="error" />}
    </div>
  );
}

function EpisodeManager({
  titleId,
  contentType,
}: {
  titleId: number;
  contentType: ContentType;
}) {
  const [episodes, setEpisodes] = useState<AdminEpisode[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [season, setSeason] = useState("1");
  const [number, setNumber] = useState("1");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Films and cartoons hide the episode layer in the viewer UI, so exactly
  // one episode (S1E1) is meaningful. It is NOT auto-created on save, so the
  // first one still has to be added — only further ones are pointless.
  const isSingleEpisode = contentType === "film" || contentType === "multfilm";
  const addBlocked = isSingleEpisode && episodes.length > 0;

  const load = useCallback(async () => {
    try {
      setEpisodes(await adminApi.listEpisodes(titleId));
    } catch {
      setEpisodes([]);
    }
  }, [titleId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleAdd = async () => {
    try {
      await adminApi.createEpisode(titleId, {
        // A single-episode title only ever has S1E1 — don't let a typo in the
        // form create an unreachable "episode 5" of a film.
        season: isSingleEpisode ? 1 : Number(season) || 1,
        number: isSingleEpisode ? 1 : Number(number) || 1,
        name: name.trim() || null,
      });
      setName("");
      setNumber(String((Number(number) || 1) + 1));
      setError(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Qism qo'shishda xatolik.");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Bu qism va uning fayllari o'chirilsinmi?")) return;
    await adminApi.deleteEpisode(id).catch(() => undefined);
    load();
  };

  const seasons = [...new Set(episodes.map((episode) => episode.season))].sort((a, b) => a - b);

  return (
    <section>
      <SectionTitle>Qismlar</SectionTitle>

      {episodes.length === 0 ? (
        <EmptyState message="Qism yo'q." />
      ) : (
        <div className="space-y-3">
          {seasons.map((seasonNumber) => (
            <div key={seasonNumber}>
              <p className="mb-1 font-body text-xs text-ink-dim">{seasonNumber}-fasl</p>
              <ul className="space-y-1.5">
                {episodes
                  .filter((episode) => episode.season === seasonNumber)
                  .map((episode) => (
                    <li key={episode.id} className="rounded-xl border border-surface-hi bg-surface p-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <button
                          onClick={() => setExpanded(expanded === episode.id ? null : episode.id)}
                          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                        >
                          {expanded === episode.id ? (
                            <ChevronDown size={14} className="shrink-0 text-ink-dim" />
                          ) : (
                            <ChevronRight size={14} className="shrink-0 text-ink-dim" />
                          )}
                          <span className="truncate font-body text-sm text-ink">
                            {episode.number}-qism{episode.name ? ` · ${episode.name}` : ""}
                          </span>
                          <Badge>{episode.file_count} fayl</Badge>
                        </button>
                        <IconButton
                          label="Qismni o'chirish"
                          tone="danger"
                          onClick={() => handleDelete(episode.id)}
                        >
                          <Trash2 size={14} />
                        </IconButton>
                      </div>
                      {expanded === episode.id && <EpisodeFiles episode={episode} />}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {addBlocked ? (
        <Notice message="Kino va multfilmda faqat bitta qism bo'ladi — u allaqachon qo'shilgan. Fayl biriktirish uchun qismni oching." />
      ) : (
        <CardShell>
          {!isSingleEpisode && (
            <div className="grid grid-cols-2 gap-2">
              <Field label="Fasl">
                <TextInput value={season} onChange={setSeason} type="number" />
              </Field>
              <Field label="Qism raqami">
                <TextInput value={number} onChange={setNumber} type="number" />
              </Field>
            </div>
          )}
          <div className={isSingleEpisode ? "" : "mt-2"}>
            <Field label="Qism nomi (ixtiyoriy)">
              <TextInput value={name} onChange={setName} placeholder="Masalan: Boshlanish" />
            </Field>
          </div>
          <div className="mt-2">
            <Button full tone="ghost" onClick={handleAdd}>
              <span className="inline-flex items-center justify-center gap-1.5">
                <Plus size={15} />
                {isSingleEpisode ? "Qism yaratish (fayl uchun)" : "Qism qo'shish"}
              </span>
            </Button>
          </div>
          {error && <div className="mt-2">{<Notice message={error} tone="error" />}</div>}
        </CardShell>
      )}
    </section>
  );
}

export function TitleEditor({ titleId, onClose, onOpenTitle }: Props) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [savedId, setSavedId] = useState<number | null>(titleId);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<SimilarTitle[]>([]);
  // Poster state is kept separately from the text form: it is saved by its
  // own upload request, not by the Save button, so folding it into `form`
  // would imply an unsaved edit that does not exist.
  const [poster, setPoster] = useState<{ url: string | null; imageId: number | null }>({
    url: null,
    imageId: null,
  });
  // Not part of FormState: tmdb_id is never typed by hand, it only decides
  // whether "fill translations from TMDB" has anything to fill from.
  const [hasTmdbId, setHasTmdbId] = useState(false);


  // Only while creating: when editing, the title's own row would match
  // itself and every result would be noise.
  useEffect(() => {
    if (savedId !== null || form.name.trim().length < 2) {
      setDuplicates([]);
      return;
    }
    const timer = setTimeout(() => {
      adminApi
        .similarTitles(form.name.trim())
        .then(setDuplicates)
        .catch(() => setDuplicates([]));
    }, 400);
    return () => clearTimeout(timer);
  }, [form.name, savedId]);

  const syncPoster = useCallback((title: AdminTitle) => {
    setPoster({ url: title.poster_url ?? null, imageId: title.poster_image_id ?? null });
  }, []);

  const reloadTitle = useCallback(
    async (id: number) => {
      try {
        // Fetched by id. This used to scan the first page of the paginated
        // list, so once the catalog passed 100 titles every older one was
        // invisible to the refresh: an uploaded poster was stored, the
        // editor never saw the new poster_image_id, and the picker fell
        // back to showing TMDB's — looking exactly like a failed upload.
        const title = await adminApi.getTitle(id);
        setForm(toForm(title));
        syncPoster(title);
        setHasTmdbId(title.tmdb_id != null);
      } catch {
        /* the editor stays usable on a failed refresh */
      }
    },
    [syncPoster],
  );

  useEffect(() => {
    if (titleId == null) return;
    void reloadTitle(titleId);
  }, [titleId, reloadTitle]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSave = async () => {
    if (!form.name.trim()) {
      setError("Nom kiritilishi shart.");
      return;
    }
    const payload = {
      name: form.name.trim(),
      content_type: form.content_type,
      year: parseNumber(form.year),
      country: form.country.trim() || null,
      genres: parseGenres(form.genres),
      description: form.description.trim() || null,
      poster_url: form.poster_url.trim() || null,
      rating: parseNumber(form.rating),
    };

    try {
      const saved =
        savedId == null
          ? await adminApi.createTitle(payload)
          : await adminApi.updateTitle(savedId, payload);
      setSavedId(saved.id);
      setForm(toForm(saved));
      syncPoster(saved);
      setMessage("Saqlandi.");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saqlashda xatolik.");
    }
  };

  const handleEnrich = async () => {
    if (savedId == null) return;
    try {
      const enriched = await adminApi.enrichTitle(savedId);
      setForm(toForm(enriched));
      syncPoster(enriched);
      setHasTmdbId(enriched.tmdb_id != null);
      setMessage(
        enriched.tmdb_id == null ? "TMDB'dan mos kelmadi." : "TMDB ma'lumotlari yuklandi.",
      );
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "TMDB xatoligi.");
    }
  };

  return (
    <div className="space-y-4">
      <button
        onClick={onClose}
        className="inline-flex items-center gap-1.5 font-body text-sm text-ink-dim"
      >
        <ArrowLeft size={16} /> Orqaga
      </button>

      <div className="space-y-2">
        <Field label="Nomi">
          <TextInput value={form.name} onChange={(value) => update("name", value)} />
        </Field>

        <DuplicateWarning
          matches={duplicates}
          onOpen={(id) => {
            setDuplicates([]);
            onOpenTitle(id);
          }}
        />
        <div className="grid grid-cols-2 gap-2">
          <Field label="Turi">
            <Select<ContentType>
              value={form.content_type}
              onChange={(value) => update("content_type", value)}
              options={CONTENT_TYPES}
            />
          </Field>
          <Field label="Yil">
            <TextInput value={form.year} onChange={(value) => update("year", value)} type="number" />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Davlat">
            <TextInput value={form.country} onChange={(value) => update("country", value)} />
          </Field>
          <Field label="Reyting">
            <TextInput
              value={form.rating}
              onChange={(value) => update("rating", value)}
              type="number"
            />
          </Field>
        </div>
        <Field label="Janrlar (vergul bilan)">
          <TextInput
            value={form.genres}
            onChange={(value) => update("genres", value)}
            placeholder="Komediya, Action"
          />
        </Field>
        <Field label="Poster havolasi (TMDB)">
          <TextInput
            value={form.poster_url}
            onChange={(value) => update("poster_url", value)}
            mono
          />
        </Field>

        {/* Upload is offered only once the title exists: the image attaches
            by id, so there is nothing to attach it to while creating. The
            TMDB field above stays editable and remains the fallback. */}
        {savedId != null && (
          <Field label="Yoki galereyadan yuklash">
            <PosterPicker
              currentUrl={
                poster.imageId ? `/api/movies/images/${poster.imageId}` : poster.url
              }
              fallbackUrl={poster.url}
              hasCustom={Boolean(poster.imageId)}
              // The upload's own response carries the stored image id, so the
              // picker switches to the new poster immediately rather than
              // waiting on a refresh that might fail — a failed refresh must
              // never look like a failed upload.
              onUpload={async (file) => {
                const result = await adminApi.uploadTitlePoster(savedId, file);
                setPoster((current) => ({ ...current, imageId: result.image_id }));
                return result;
              }}
              onClear={async () => {
                const result = await adminApi.clearTitlePoster(savedId);
                setPoster((current) => ({ ...current, imageId: null }));
                return result;
              }}
              onChanged={() => void reloadTitle(savedId)}
            />
          </Field>
        )}
        <Field label="Tavsif">
          <TextArea
            value={form.description}
            onChange={(value) => update("description", value)}
            rows={4}
          />
        </Field>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button full onClick={handleSave}>
          Saqlash
        </Button>
        {savedId != null && (
          <Button tone="ghost" onClick={handleEnrich}>
            <span className="inline-flex items-center gap-1.5">
              <Sparkles size={15} /> TMDB'dan to'ldirish
            </span>
          </Button>
        )}
      </div>

      {savedId != null && (
        <TMDBSearchBox
          titleId={savedId}
          onApplied={(updated) => {
            // Name is intentionally absent from the applied fields — keep
            // whatever the admin typed, in Uzbek.
            setForm(toForm(updated));
            syncPoster(updated);
            setHasTmdbId(updated.tmdb_id != null);
            setMessage("TMDB ma'lumotlari qo'llandi.");
          }}
        />
      )}

      {message && <Notice message={message} />}
      {error && <Notice message={error} tone="error" />}

      {savedId == null ? (
        <Notice message="Qism qo'shish uchun avval saqlang." />
      ) : (
        <>
          <TranslationEditor titleId={savedId} hasTmdbId={hasTmdbId} />
          <CollectionPicker titleId={savedId} />
          <EpisodeManager titleId={savedId} contentType={form.content_type} />
        </>
      )}
    </div>
  );
}
