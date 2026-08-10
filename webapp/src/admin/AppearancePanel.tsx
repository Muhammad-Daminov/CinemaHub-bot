/**
 * Appearance: themes, who gets them, and banner campaigns.
 *
 * One section with three tabs rather than three top-level entries — they
 * are the same job, and scattering them across the panel is what the
 * brief asks to avoid.
 *
 * Everything here drives the existing backend. No resolution logic, no
 * validation authority and no second theme system live in this file: the
 * server decides, and a rejected request is shown as its error rather
 * than being papered over. Client-side checks exist only to catch a
 * mistake before a round trip.
 */
import { useCallback, useEffect, useState } from "react";
import { adminApi, ApiError } from "../lib/api";
import { DECORATION_KEYS, DecorationArt } from "../components/DecorationLayer";
import { useT } from "../lib/i18n";
import type {
  AdminBanner,
  AdminTheme,
  AdminThemeAssignment,
  ThemeScope,
  ThemeVocabulary,
} from "../types/admin";
import { Button, EmptyState, Field, Notice, SectionTitle, Select, TextInput } from "./ui";

type Tab = "themes" | "assignments" | "banners";

const TABS: { id: Tab; label: string }[] = [
  { id: "themes", label: "Mavzular" },
  { id: "assignments", label: "Tayinlash" },
  { id: "banners", label: "Bannerlar" },
];

/** Mirrors the server's grammar so a typo is caught before the request. */
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

/** Mirror the server's defaults — what "reset" restores. */
const DEFAULT_CARD_SHAPE = "rounded";
const DEFAULT_DECORATION = "none";

/** Scopes in the backend's precedence order, strongest first. */
const SCOPES: { value: ThemeScope; label: string; hint: string }[] = [
  { value: "user", label: "Foydalanuvchi", hint: "Aniq bir foydalanuvchi — eng kuchli" },
  { value: "badge", label: "Unvon", hint: "Masalan: badge.anime." },
  { value: "interest", label: "Qiziqish", hint: "anime, film, serial, drama, multfilm" },
  { value: "subscription", label: "Obuna", hint: "premium yoki free" },
  { value: "global", label: "Hammaga", hint: "Standart — eng past" },
];

const AUDIENCES = [
  { value: "global", label: "Hammaga" },
  { value: "content_type", label: "Kontent turi" },
  { value: "badge", label: "Unvon" },
  { value: "premium", label: "Obunachilar" },
  { value: "free", label: "Obunasizlar" },
];

/**
 * A live preview that cannot touch the real UI.
 *
 * The tokens are set on **this element**, not on the document root, so an
 * admin trying colours never restyles the panel they are working in. CSS
 * custom properties inherit, so everything inside picks them up — and the
 * real theme is only applied globally after a successful save, by the
 * ThemeProvider on the next load.
 */
function ThemePreview({
  tokens,
  shape,
  decoration,
}: {
  tokens: Record<string, string>;
  shape: string;
  decoration: string;
}) {
  const radius = { square: "0px", soft: "4px", rounded: "12px", "extra-rounded": "20px" }[shape] ?? "12px";
  const safe = Object.fromEntries(
    Object.entries(tokens).filter(([, value]) => HEX.test(value)),
  ) as Record<string, string>;

  return (
    <div
      // A plain style object of validated hex values — never a CSS string,
      // and never dangerouslySetInnerHTML.
      style={{ ...safe, backgroundColor: "var(--color-bg)" } as React.CSSProperties}
      className="relative overflow-hidden space-y-3 rounded-xl border border-surface-hi p-3"
    >
      {/*
        The decoration exactly as the Mini App renders it — same compiled
        component, same opacity — but `absolute` inside this box rather
        than `fixed` to the viewport, so it decorates the preview and
        cannot reach the panel around it. Inert and behind the content.
      */}
      <div aria-hidden className="pointer-events-none absolute inset-0 z-0 opacity-[0.07]">
        <DecorationArt name={decoration} />
      </div>
      <div className="relative z-10 space-y-3">
        <p className="font-mono text-[10px] uppercase tracking-wider" style={{ color: "var(--color-ink-dim)" }}>
          Ko'rinish
        </p>

        <div className="flex gap-2">
          {[0, 1, 2].map((index) => (
            <div key={index} className="flex-1">
              <div
                className="aspect-[2/3] w-full"
                style={{ backgroundColor: "var(--color-surface-hi)", borderRadius: radius }}
              />
              <p className="mt-1 truncate text-[10px]" style={{ color: "var(--color-ink)" }}>
                Kino {index + 1}
              </p>
            </div>
          ))}
        </div>

        <div style={{ backgroundColor: "var(--color-surface)", borderRadius: radius }} className="p-2">
          <p className="text-xs" style={{ color: "var(--color-ink)" }}>Asosiy matn</p>
          <p className="text-[11px]" style={{ color: "var(--color-ink-dim)" }}>Ikkilamchi matn</p>
        </div>

        <div className="flex flex-wrap gap-1.5">
          <span
            className="rounded-full px-3 py-1 text-[11px] font-semibold"
            style={{ backgroundColor: "var(--color-marquee)", color: "#0A0A0D" }}
          >
            Tugma
          </span>
          {["--color-success", "--color-warning", "--color-danger"].map((token) => (
            <span
              key={token}
              className="rounded-full px-2 py-1 text-[10px]"
              style={{ backgroundColor: `var(${token})`, color: "#0A0A0D" }}
            >
              {token.replace("--color-", "")}
            </span>
          ))}
        </div>

        <div className="flex gap-1.5">
          {[1, 2, 3, 4].map((number) => {
            const watched = number === 3;
            return (
              <div
                key={number}
                className="flex h-8 w-8 items-center justify-center text-[11px]"
                style={{
                  backgroundColor: watched
                    ? "var(--color-episode-watched)"
                    : "var(--color-episode-unwatched)",
                  color: "var(--color-ink)",
                  borderRadius: radius,
                }}
              >
                {number}
                {watched ? "✓" : ""}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * The decoration picker.
 *
 * Each swatch renders the **real compiled component** at thumbnail size,
 * so what an admin picks is literally what a viewer gets. There is no
 * upload, no URL and no markup anywhere in this flow: the options come
 * from `DECORATION_KEYS`, which the frontend compiles in, and the only
 * thing that travels to the server or into the database is the key.
 *
 * Names come from the shared locale catalog rather than a hardcoded list,
 * so they read in the admin's own language and a new decoration needs one
 * translation, not an edit here.
 */
function DecorationPicker({
  value,
  onChange,
  tokens,
}: {
  value: string;
  onChange: (name: string) => void;
  tokens: Record<string, string>;
}) {
  const t = useT();
  const safe = Object.fromEntries(
    Object.entries(tokens).filter(([, hex]) => HEX.test(hex)),
  ) as Record<string, string>;

  return (
    <div className="grid grid-cols-3 gap-2">
      {DECORATION_KEYS.map((name) => {
        const selected = value === name;
        const label = t(`theme.decoration.${name}`);
        return (
          <button
            key={name}
            type="button"
            aria-pressed={selected}
            aria-label={label}
            onClick={() => onChange(name)}
            className={`rounded-lg border p-1.5 text-left transition-colors ${
              selected ? "border-marquee bg-surface-hi" : "border-surface-hi bg-surface"
            }`}
          >
            <div
              // The swatch carries the theme's own colours so a decoration
              // is judged against the palette it will actually sit on.
              style={{ ...safe, backgroundColor: "var(--color-bg)" } as React.CSSProperties}
              className="relative h-12 w-full overflow-hidden rounded"
            >
              {name === "none" ? (
                <span
                  className="absolute inset-0 flex items-center justify-center font-mono text-[10px]"
                  style={{ color: "var(--color-ink-dim)" }}
                >
                  —
                </span>
              ) : (
                // Higher opacity than the live layer purely so a 48px
                // swatch is legible; the pattern itself is identical.
                <div aria-hidden className="pointer-events-none absolute inset-0 opacity-40">
                  <DecorationArt name={name} />
                </div>
              )}
            </div>
            <p className="mt-1 truncate text-[10px] text-ink-dim">
              {selected ? "✓ " : ""}
              {label}
            </p>
          </button>
        );
      })}
    </div>
  );
}

function ThemesTab() {
  const [themes, setThemes] = useState<AdminTheme[]>([]);
  const [vocabulary, setVocabulary] = useState<ThemeVocabulary | null>(null);
  const [editing, setEditing] = useState<AdminTheme | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [shape, setShape] = useState(DEFAULT_CARD_SHAPE);
  const [decoration, setDecoration] = useState(DEFAULT_DECORATION);
  const [newKey, setNewKey] = useState("");
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setThemes(await adminApi.themes());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    }
  }, []);

  useEffect(() => {
    load();
    adminApi.themeVocabulary().then(setVocabulary).catch(() => setVocabulary(null));
  }, [load]);

  const act = async (run: () => Promise<unknown>, ok: string) => {
    try {
      await run();
      setError(null);
      setMessage(ok);
      await load();
    } catch (err) {
      setMessage(null);
      // The API is the authority — show exactly what it said.
      setError(err instanceof ApiError ? err.message : "Amalni bajarib bo'lmadi.");
    }
  };

  const startEditing = (theme: AdminTheme) => {
    setEditing(theme);
    setDraft({ ...(vocabulary?.defaults ?? {}), ...theme.tokens });
    setShape(theme.card_shape);
    setDecoration(theme.decoration);
  };

  const invalid = Object.entries(draft).filter(([, value]) => !HEX.test(value));

  return (
    <div className="space-y-3">
      {error && <Notice message={error} tone="error" />}
      {message && <Notice message={message} />}

      <SectionTitle>Yangi mavzu</SectionTitle>
      <div className="grid grid-cols-2 gap-2">
        <TextInput value={newKey} onChange={setNewKey} placeholder="kalit (anime-dark)" mono />
        <TextInput value={newName} onChange={setNewName} placeholder="Nomi" />
      </div>
      <Button
        full
        disabled={!newKey.trim() || !newName.trim()}
        onClick={() =>
          act(async () => {
            await adminApi.createTheme({ key: newKey.trim(), name: newName.trim(), tokens: {} });
            setNewKey("");
            setNewName("");
          }, "Mavzu yaratildi.")
        }
      >
        Yaratish
      </Button>

      <SectionTitle>Mavzular</SectionTitle>
      {themes.length === 0 ? (
        <EmptyState message="Hali mavzu yo'q — standart ko'rinish ishlatilmoqda." />
      ) : (
        <ul className="space-y-2">
          {themes.map((theme) => (
            <li key={theme.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-body text-sm text-ink">
                    {theme.name} {theme.is_default && "· standart"}
                    {!theme.is_active && " · o'chirilgan"}
                  </p>
                  <p className="font-mono text-[11px] text-ink-dim">
                    {theme.key} · {theme.card_shape} · {theme.decoration}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  {Object.entries(theme.tokens)
                    .slice(0, 4)
                    .map(([token, value]) => (
                      <span
                        key={token}
                        title={token}
                        className="h-5 w-5 rounded border border-surface-hi"
                        style={{ backgroundColor: HEX.test(value) ? value : undefined }}
                      />
                    ))}
                </div>
              </div>

              {theme.contrast_warnings.length > 0 && (
                <div className="mt-2 space-y-1">
                  {theme.contrast_warnings.map((warning) => (
                    <Notice
                      key={`${warning.foreground}-${warning.background}`}
                      tone="error"
                      message={`${warning.label}: kontrast ${warning.ratio} (kamida ${warning.required} kerak)`}
                    />
                  ))}
                </div>
              )}

              <div className="mt-2 grid grid-cols-2 gap-2">
                <Button tone="ghost" onClick={() => startEditing(theme)}>
                  Ranglar
                </Button>
                <Button
                  tone="ghost"
                  onClick={() =>
                    act(
                      () => adminApi.duplicateTheme(theme.id, `${theme.key}-copy`, `${theme.name} nusxa`),
                      "Nusxa olindi.",
                    )
                  }
                >
                  Nusxalash
                </Button>
                <Button
                  tone="ghost"
                  disabled={theme.is_default}
                  title={theme.is_default ? "Standart mavzuni o'chirib bo'lmaydi" : undefined}
                  onClick={() => act(() => adminApi.toggleTheme(theme.id), "Holat yangilandi.")}
                >
                  {theme.is_active ? "O'chirish" : "Yoqish"}
                </Button>
                <Button
                  tone="ghost"
                  disabled={theme.is_default}
                  onClick={() => act(() => adminApi.setDefaultTheme(theme.id), "Standart qilindi.")}
                >
                  Standart
                </Button>
                <Button
                  tone="danger"
                  disabled={theme.is_default}
                  title={theme.is_default ? "Standart mavzu o'chirilmaydi" : undefined}
                  onClick={() => act(() => adminApi.deleteTheme(theme.id), "O'chirildi.")}
                >
                  Yo'q qilish
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing && vocabulary && (
        <div className="rounded-xl border border-surface-hi bg-surface p-3">
          <SectionTitle>{editing.name} — ranglar</SectionTitle>

          <ThemePreview tokens={draft} shape={shape} decoration={decoration} />

          <div className="mt-3">
            <Field label="Karta shakli">
              <Select
                value={shape}
                onChange={setShape}
                options={Object.keys(vocabulary.card_shapes).map((value) => ({
                  value,
                  label: value,
                }))}
              />
            </Field>
          </div>

          <div className="mt-3">
            <Field label="Bezak">
              <DecorationPicker value={decoration} onChange={setDecoration} tokens={draft} />
            </Field>
          </div>

          <div className="mt-3 space-y-2">
            {Object.keys(vocabulary.defaults).map((token) => (
              <div key={token} className="flex items-center gap-2">
                <input
                  type="color"
                  // A native picker only ever produces #rrggbb, so the
                  // common path cannot produce an invalid value at all.
                  value={HEX.test(draft[token] ?? "") ? (draft[token] as string) : "#000000"}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, [token]: event.target.value }))
                  }
                  className="h-9 w-12 shrink-0 rounded border border-surface-hi bg-transparent"
                  aria-label={token}
                />
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-dim">
                  {token}
                </span>
                <div className="w-28">
                  <TextInput
                    value={draft[token] ?? ""}
                    onChange={(value) => setDraft((current) => ({ ...current, [token]: value }))}
                    mono
                  />
                </div>
              </div>
            ))}
          </div>

          {invalid.length > 0 && (
            <div className="mt-2">
              <Notice
                tone="error"
                message={`Faqat #rrggbb formati: ${invalid.map(([token]) => token).join(", ")}`}
              />
            </div>
          )}

          {/*
            Resets the *draft* to the server's default palette and saves
            nothing — the operator still has to press Saqlash, and can
            close without saving to change their mind. Deliberately not
            the same thing as the "Standart" button on each theme above,
            which makes a theme the platform default; conflating the two
            would let "reset my colours" silently repoint every user.
          */}
          <div className="mt-3">
            <Button
              tone="ghost"
              full
              onClick={() => {
                setDraft({ ...vocabulary.defaults });
                setShape(DEFAULT_CARD_SHAPE);
                setDecoration(DEFAULT_DECORATION);
                setMessage("Standart ranglar tiklandi — saqlash uchun Saqlash bosing.");
                setError(null);
              }}
            >
              Standart ranglarga qaytarish
            </Button>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button
              disabled={invalid.length > 0}
              onClick={() =>
                act(async () => {
                  await adminApi.setThemeTokens(editing.id, draft, shape, decoration);
                  setEditing(null);
                }, "Ranglar saqlandi.")
              }
            >
              Saqlash
            </Button>
            <Button tone="ghost" onClick={() => setEditing(null)}>
              Yopish
            </Button>
          </div>

          {/*
            The shortcut that replaces "switch to the Assignments tab and
            type your own numeric user id". The id is never sent: a USER
            assignment with no user_id is resolved server-side from the
            verified admin session, so this cannot be aimed at anyone else
            by editing the request.
          */}
          <div className="mt-2">
            <Button
              tone="ghost"
              full
              onClick={() =>
                act(
                  () => adminApi.createThemeAssignment({ theme_id: editing.id, scope: "user" }),
                  "Mavzu sizning panelingizga qo'llandi. Ilovani qayta oching.",
                )
              }
            >
              Admin panelimga qo'llash
            </Button>
            <p className="mt-1 font-body text-[11px] text-ink-dim">
              Faqat sizga qo'llanadi. Saqlanmagan ranglar avval saqlanishi kerak.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function AssignmentsTab() {
  const [themes, setThemes] = useState<AdminTheme[]>([]);
  const [assignments, setAssignments] = useState<AdminThemeAssignment[]>([]);
  const [themeId, setThemeId] = useState("");
  const [scope, setScope] = useState<ThemeScope>("global");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [themeList, assignmentList] = await Promise.all([
        adminApi.themes(),
        adminApi.themeAssignments(),
      ]);
      setThemes(themeList);
      setAssignments(assignmentList);
      if (!themeId && themeList[0]) setThemeId(String(themeList[0].id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    }
  }, [themeId]);

  useEffect(() => {
    load();
  }, [load]);

  const needsTarget = scope !== "global" && scope !== "user";
  const scopeInfo = SCOPES.find((item) => item.value === scope);

  const submit = async () => {
    try {
      await adminApi.createThemeAssignment({
        theme_id: Number(themeId),
        scope,
        // The backend re-validates every one of these; sending only what
        // the scope needs keeps a stray target out of a GLOBAL rule.
        user_id: scope === "user" ? Number(target) || null : null,
        target_value: needsTarget ? target.trim() || null : null,
      });
      setTarget("");
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saqlab bo'lmadi.");
    }
  };

  return (
    <div className="space-y-3">
      {error && <Notice message={error} tone="error" />}

      <Notice message="Ustuvorlik: Foydalanuvchi > Unvon > Qiziqish > Obuna > Hammaga. Bir nechta qoida mos kelsa, eng yuqorisi g'olib." />

      <SectionTitle>Yangi tayinlash</SectionTitle>
      <Field label="Mavzu">
        <Select
          value={themeId}
          onChange={setThemeId}
          options={themes.map((theme) => ({ value: String(theme.id), label: theme.name }))}
        />
      </Field>
      <Field label="Doira">
        <Select
          value={scope}
          onChange={(value) => {
            setScope(value as ThemeScope);
            setTarget("");
          }}
          options={SCOPES.map(({ value, label }) => ({ value, label }))}
        />
      </Field>
      {scopeInfo && <p className="font-body text-[11px] text-ink-dim">{scopeInfo.hint}</p>}

      {scope === "user" && (
        <Field label="Foydalanuvchi ID (ichki)">
          <TextInput value={target} onChange={setTarget} placeholder="masalan 42" mono />
        </Field>
      )}
      {needsTarget && (
        <Field label="Maqsad">
          <TextInput value={target} onChange={setTarget} placeholder={scopeInfo?.hint} mono />
        </Field>
      )}

      <Button
        full
        disabled={!themeId || (scope !== "global" && !target.trim())}
        onClick={submit}
      >
        Tayinlash
      </Button>

      <SectionTitle>Mavjud tayinlashlar</SectionTitle>
      {assignments.length === 0 ? (
        <EmptyState message="Tayinlash yo'q — hamma standart mavzuni ko'radi." />
      ) : (
        <ul className="space-y-2">
          {assignments.map((assignment) => {
            const theme = themes.find((item) => item.id === assignment.theme_id);
            return (
              <li
                key={assignment.id}
                className="flex items-center justify-between gap-2 rounded-xl border border-surface-hi bg-surface p-3"
              >
                <div className="min-w-0">
                  <p className="truncate font-body text-sm text-ink">{theme?.name ?? "—"}</p>
                  <p className="font-mono text-[11px] text-ink-dim">
                    {assignment.scope}
                    {assignment.user_id ? ` · user ${assignment.user_id}` : ""}
                    {assignment.target_value ? ` · ${assignment.target_value}` : ""}
                  </p>
                </div>
                <Button
                  tone="danger"
                  onClick={async () => {
                    try {
                      await adminApi.deleteThemeAssignment(assignment.id);
                      await load();
                    } catch (err) {
                      setError(err instanceof ApiError ? err.message : "O'chirib bo'lmadi.");
                    }
                  }}
                >
                  O'chirish
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * A banner preview that cannot touch anything real.
 *
 * Self-contained markup with its own colours drawn from tokens — it does
 * not mount HeroBanner, does not rotate, and writes nothing to the
 * document. Editing a campaign therefore never restyles the panel or the
 * viewer; the real carousel only changes after a save, when the resolver
 * next runs for a viewer.
 *
 * The image is rendered only when it matches the same URL grammar the
 * backend enforces, so a half-typed or hostile value shows nothing rather
 * than being handed to the browser.
 */
const SAFE_IMAGE = /^(https:\/\/|http:\/\/|\/api\/movies\/images\/\d+$)/;

function BannerPreview({
  headline,
  subtitle,
  label,
  imageUrl,
}: {
  headline: string;
  subtitle: string;
  label: string;
  imageUrl: string;
}) {
  const showImage = imageUrl.trim() !== "" && SAFE_IMAGE.test(imageUrl.trim());

  return (
    <div className="overflow-hidden rounded-xl border border-surface-hi">
      <div className="relative aspect-[16/9] w-full bg-surface-hi">
        {showImage && (
          <img src={imageUrl.trim()} alt="" className="h-full w-full object-cover" />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/40 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 p-3">
          {label && (
            <span className="mb-1 inline-block rounded-full bg-marquee px-2 py-0.5 font-mono text-[10px] uppercase text-on-marquee">
              {label.replace("banner.label.", "")}
            </span>
          )}
          {/* Plain text nodes — React escapes them, and the backend refuses
              angle brackets outright before they ever reach storage. */}
          <p className="font-display text-lg font-semibold text-ink">{headline || "—"}</p>
          {subtitle && <p className="font-body text-xs text-ink-dim">{subtitle}</p>}
        </div>
      </div>
    </div>
  );
}

function BannersTab() {
  const [banners, setBanners] = useState<AdminBanner[]>([]);
  const [labels, setLabels] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  // null = creating a new campaign; an id = editing that one in place.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState({
    headline: "",
    subtitle: "",
    label_key: "",
    image_url: "",
    title_id: "",
    audience: "global",
    target_value: "",
    priority: "0",
    starts_at: "",
    ends_at: "",
  });

  const load = useCallback(async () => {
    try {
      setBanners(await adminApi.banners());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yuklab bo'lmadi.");
    }
  }, []);

  useEffect(() => {
    load();
    adminApi.bannerLabels().then((data) => setLabels(data.labels)).catch(() => setLabels([]));
  }, [load]);

  const update = (key: string, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  const needsTarget = form.audience === "content_type" || form.audience === "badge";

  const blank = {
    headline: "",
    subtitle: "",
    label_key: "",
    image_url: "",
    title_id: "",
    audience: "global",
    target_value: "",
    priority: "0",
    starts_at: "",
    ends_at: "",
  };

  /** ISO timestamp -> the value a datetime-local input expects. */
  const toLocalInput = (iso: string | null) =>
    iso ? new Date(iso).toISOString().slice(0, 16) : "";

  const startEditing = (banner: AdminBanner) => {
    setEditingId(banner.id);
    setForm({
      headline: banner.headline ?? "",
      subtitle: banner.subtitle ?? "",
      label_key: banner.label_key ?? "",
      image_url: banner.image_url ?? "",
      title_id: banner.title_id ? String(banner.title_id) : "",
      audience: banner.audience,
      target_value: banner.target_value ?? "",
      priority: String(banner.priority),
      starts_at: toLocalInput(banner.starts_at),
      ends_at: toLocalInput(banner.ends_at),
    });
  };

  const submit = async () => {
    // One payload for both paths, so an edit is validated by exactly the
    // same backend rules as a creation — there is no second, looser route.
    const payload = {
      headline: form.headline.trim() || null,
      subtitle: form.subtitle.trim() || null,
      label_key: form.label_key || null,
      image_url: form.image_url.trim() || null,
      // Empty means an announcement with no catalog entry — the
      // "coming soon" case the backend supports by design.
      title_id: form.title_id ? Number(form.title_id) : null,
      audience: form.audience as AdminBanner["audience"],
      target_value: needsTarget ? form.target_value.trim() || null : null,
      priority: Number(form.priority) || 0,
      starts_at: form.starts_at ? new Date(form.starts_at).toISOString() : null,
      ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null,
    };

    try {
      if (editingId === null) {
        await adminApi.createBanner(payload);
      } else {
        // Scoped to the campaign being edited; no other row is touched.
        await adminApi.updateBanner(editingId, payload);
      }
      setForm(blank);
      setEditingId(null);
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saqlab bo'lmadi.");
    }
  };

  return (
    <div className="space-y-3">
      {error && <Notice message={error} tone="error" />}

      <SectionTitle>{editingId === null ? "Yangi banner" : `#${editingId} tahrirlash`}</SectionTitle>

      <BannerPreview
        headline={form.headline}
        subtitle={form.subtitle}
        label={form.label_key}
        imageUrl={form.image_url}
      />
      <Field label="Sarlavha">
        <TextInput value={form.headline} onChange={(v) => update("headline", v)} />
      </Field>
      <Field label="Tavsif">
        <TextInput value={form.subtitle} onChange={(v) => update("subtitle", v)} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Yorliq">
          <Select
            value={form.label_key}
            onChange={(v) => update("label_key", v)}
            options={[{ value: "", label: "yo'q" }, ...labels.map((key) => ({ value: key, label: key.replace("banner.label.", "") }))]}
          />
        </Field>
        <Field label="Ustuvorlik">
          <TextInput value={form.priority} onChange={(v) => update("priority", v.replace(/[^0-9]/g, ""))} mono />
        </Field>
      </div>
      <Field label="Kino ID (bo'sh = 'tez kunda' e'loni)">
        <TextInput value={form.title_id} onChange={(v) => update("title_id", v.replace(/[^0-9]/g, ""))} mono />
      </Field>
      <Field label="Rasm havolasi">
        <TextInput value={form.image_url} onChange={(v) => update("image_url", v)} placeholder="https://…" mono />
      </Field>
      <Field label="Kimga">
        <Select
          value={form.audience}
          onChange={(v) => update("audience", v)}
          options={AUDIENCES}
        />
      </Field>
      {needsTarget && (
        <Field label="Maqsad">
          <TextInput value={form.target_value} onChange={(v) => update("target_value", v)} mono />
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="Boshlanish">
          <input
            type="datetime-local"
            value={form.starts_at}
            onChange={(event) => update("starts_at", event.target.value)}
            className="w-full rounded-lg border border-surface-hi bg-surface px-3 py-2 font-mono text-xs text-ink"
          />
        </Field>
        <Field label="Tugash">
          <input
            type="datetime-local"
            value={form.ends_at}
            onChange={(event) => update("ends_at", event.target.value)}
            className="w-full rounded-lg border border-surface-hi bg-surface px-3 py-2 font-mono text-xs text-ink"
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button onClick={submit}>{editingId === null ? "Yaratish" : "Saqlash"}</Button>
        <Button
          tone="ghost"
          disabled={editingId === null && !form.headline}
          onClick={() => {
            setEditingId(null);
            setForm(blank);
          }}
        >
          Tozalash
        </Button>
      </div>

      <SectionTitle>Kampaniyalar</SectionTitle>
      {banners.length === 0 ? (
        <EmptyState message="Banner yo'q — bosh sahifa odatdagidek ishlaydi." />
      ) : (
        <ul className="space-y-2">
          {banners.map((banner) => (
            <li key={banner.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <p className="truncate font-body text-sm text-ink">{banner.headline ?? "—"}</p>
              <p className="font-mono text-[11px] text-ink-dim">
                {banner.audience}
                {banner.target_value ? ` · ${banner.target_value}` : ""} · #{banner.priority}
                {banner.is_active ? "" : " · o'chirilgan"}
                {banner.title_id ? ` · kino ${banner.title_id}` : " · e'lon"}
              </p>
              <div className="mt-2 grid grid-cols-3 gap-2">
                <Button tone="ghost" onClick={() => startEditing(banner)}>
                  Tahrirlash
                </Button>
                <Button
                  tone="ghost"
                  onClick={async () => {
                    try {
                      await adminApi.updateBanner(banner.id, { is_active: !banner.is_active });
                      await load();
                    } catch (err) {
                      setError(err instanceof ApiError ? err.message : "Xatolik.");
                    }
                  }}
                >
                  {banner.is_active ? "O'chirish" : "Yoqish"}
                </Button>
                <Button
                  tone="danger"
                  onClick={async () => {
                    try {
                      await adminApi.deleteBanner(banner.id);
                      await load();
                    } catch (err) {
                      setError(err instanceof ApiError ? err.message : "Xatolik.");
                    }
                  }}
                >
                  Yo'q qilish
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AppearancePanel() {
  const [tab, setTab] = useState<Tab>("themes");

  return (
    <div className="space-y-3">
      <div className="no-scrollbar flex gap-2 overflow-x-auto">
        {TABS.map((item) => (
          <button
            key={item.id}
            onClick={() => setTab(item.id)}
            className={`shrink-0 rounded-full px-3 py-1.5 font-body text-xs transition-colors ${
              tab === item.id
                ? "bg-marquee text-on-marquee"
                : "border border-surface-hi bg-surface text-ink-dim"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "themes" && <ThemesTab />}
      {tab === "assignments" && <AssignmentsTab />}
      {tab === "banners" && <BannersTab />}
    </div>
  );
}
