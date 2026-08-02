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
import { ArrowLeft, ChevronDown, ChevronRight, Plus, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import type {
  AdminEpisode,
  AdminMediaFile,
  AdminTitle,
  AudioLanguage,
  ContentType,
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
  languageLabel,
} from "./ui";

interface Props {
  titleId: number | null;
  onClose: () => void;
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

function EpisodeManager({ titleId }: { titleId: number }) {
  const [episodes, setEpisodes] = useState<AdminEpisode[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [season, setSeason] = useState("1");
  const [number, setNumber] = useState("1");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

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
        season: Number(season) || 1,
        number: Number(number) || 1,
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

      <CardShell>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Fasl">
            <TextInput value={season} onChange={setSeason} type="number" />
          </Field>
          <Field label="Qism raqami">
            <TextInput value={number} onChange={setNumber} type="number" />
          </Field>
        </div>
        <div className="mt-2">
          <Field label="Qism nomi (ixtiyoriy)">
            <TextInput value={name} onChange={setName} placeholder="Masalan: Boshlanish" />
          </Field>
        </div>
        <div className="mt-2">
          <Button full tone="ghost" onClick={handleAdd}>
            <span className="inline-flex items-center justify-center gap-1.5">
              <Plus size={15} /> Qism qo'shish
            </span>
          </Button>
        </div>
        {error && <div className="mt-2">{<Notice message={error} tone="error" />}</div>}
      </CardShell>
    </section>
  );
}

export function TitleEditor({ titleId, onClose }: Props) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [savedId, setSavedId] = useState<number | null>(titleId);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (titleId == null) return;
    adminApi
      .listTitles({ page_size: 100 })
      .then((page) => {
        const found = page.items.find((item) => item.id === titleId);
        if (found) setForm(toForm(found));
      })
      .catch(() => undefined);
  }, [titleId]);

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
        <Field label="Poster havolasi">
          <TextInput
            value={form.poster_url}
            onChange={(value) => update("poster_url", value)}
            mono
          />
        </Field>
        <Field label="Tavsif">
          <TextArea
            value={form.description}
            onChange={(value) => update("description", value)}
            rows={4}
          />
        </Field>
      </div>

      <div className="flex gap-2">
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

      {message && <Notice message={message} />}
      {error && <Notice message={error} tone="error" />}

      {savedId == null ? (
        <Notice message="Qism qo'shish uchun avval saqlang." />
      ) : (
        <EpisodeManager titleId={savedId} />
      )}
    </div>
  );
}
