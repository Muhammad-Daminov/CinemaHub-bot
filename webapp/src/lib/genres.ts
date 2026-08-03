/**
 * Display labels for the canonical genre keys the API now returns.
 *
 * Mirrors the genre.* entries in app/locales/uz.json. The Mini App has no
 * i18n layer — every string in it is Uzbek — so this is the Uzbek column
 * of that table and nothing more. If the app ever gains language
 * switching, this is the map that grows a second dimension.
 *
 * Unknown keys fall through to the raw value rather than rendering blank:
 * a title that somehow still holds a legacy genre should look wrong, not
 * invisible.
 */
const GENRE_LABELS: Record<string, string> = {
  action: "Jangari",
  adventure: "Sarguzasht",
  animation: "Multfilm",
  biography: "Biografik",
  comedy: "Komediya",
  crime: "Kriminal",
  documentary: "Hujjatli",
  drama: "Drama",
  family: "Oilaviy",
  fantasy: "Fantaziya",
  history: "Tarixiy",
  horror: "Ujas",
  music: "Musiqiy",
  mystery: "Detektiv",
  romance: "Melodrama",
  science_fiction: "Ilmiy fantastika",
  sport: "Sport",
  thriller: "Triller",
  war: "Urush",
  western: "Vestern",
};

export function genreLabel(key: string): string {
  return GENRE_LABELS[key] ?? key;
}

export function genreLabels(keys: string[] | null | undefined): string[] {
  return (keys ?? []).map(genreLabel);
}
