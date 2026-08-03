"""
One vocabulary for genres.

The catalog arrived from two sources that never agreed: the legacy import
wrote Uzbek labels ("Jangari", "Ilmiy"), TMDB writes English ones
("Action", "Science Fiction"), and both landed raw in chp_titles.genres.
The result was 32 distinct values for roughly 16 real genres — which
split the filter menu into duplicate buttons, showed users "Jangari" and
"Action" as if they were different things, and quietly weakened
similar_titles(), whose genre-overlap term can only match on string
equality.

The fix is to store a canonical key ("action") and translate it at the
edge. ALIASES maps every spelling actually observed in the database, plus
TMDB's full genre list so the next import lands clean instead of
reintroducing the split.

Lookup is case- and whitespace-insensitive: the data contained both
"Sport" and "sport", and both "Sarguzasht" and the typo "Sarguzshat".

Anything unrecognised maps to None and is dropped rather than guessed at.
A wrong genre is worse than a missing one — it pollutes recommendations
for every user who sees that title.
"""

# Canonical keys. Also the i18n key suffix: "action" -> "genre.action".
# Superset of TMDB's list plus the categories this catalog actually uses
# (biography and sport aren't TMDB genres but are meaningful locally).
CANONICAL_GENRES: tuple[str, ...] = (
    "action",
    "adventure",
    "animation",
    "biography",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "family",
    "fantasy",
    "history",
    "horror",
    "music",
    "mystery",
    "romance",
    "science_fiction",
    "sport",
    "thriller",
    "war",
    "western",
)

# Every variant -> canonical key. Keys here are lowercase; normalise_genre
# lowercases its input before lookup, so casing in the data doesn't matter.
_VARIANTS: dict[str, str] = {
    # --- action ---
    "action": "action",
    "jangari": "action",       # uz, 28 titles
    "jangovor": "action",      # uz variant, 4
    "boevik": "action",        # ru transliterated, 3
    # --- adventure ---
    "adventure": "adventure",
    "sarguzasht": "adventure",  # uz, 12
    "sarguzshat": "adventure",  # typo present in the data, 1
    # --- animation ---
    "animation": "animation",
    "multfilm": "animation",
    "animatsiya": "animation",
    # --- biography ---
    "biography": "biography",
    "biografik": "biography",
    "biografiya": "biography",
    # --- comedy ---
    "comedy": "comedy",
    "komediya": "comedy",       # uz, 15
    # --- crime ---
    "crime": "crime",
    "kriminal": "crime",        # uz, 20
    # --- documentary ---
    "documentary": "documentary",
    "hujjatli": "documentary",
    # --- drama ---
    "drama": "drama",           # 18
    "hayotiy": "drama",         # uz "true-to-life"; drama is the closest fit
    # --- family ---
    "family": "family",
    "oilaviy": "family",
    # --- fantasy ---
    "fantasy": "fantasy",
    "fantaziya": "fantasy",     # uz, 5 — distinct from "fantastika" below
    # --- history ---
    "history": "history",
    "tarixiy": "history",
    # --- horror ---
    "horror": "horror",
    "ujas": "horror",           # uz, 12
    "daxshat": "horror",        # uz, 4
    # --- music ---
    "music": "music",
    "musiqiy": "music",
    # --- mystery ---
    "mystery": "mystery",
    "detective": "mystery",     # 5 — whodunnit
    "detektiv": "mystery",
    "mistika": "mystery",       # 5 — supernatural/mystical
    # --- romance ---
    "romance": "romance",
    "melodrama": "romance",
    # --- science fiction ---
    "science fiction": "science_fiction",
    "sci-fi": "science_fiction",
    "ilmiy": "science_fiction",      # uz, 11 — short for "ilmiy fantastika"
    "fantastik": "science_fiction",  # uz, 8
    "fantastika": "science_fiction",  # ru transliterated, 5
    "dystopia": "science_fiction",
    # --- sport ---
    "sport": "sport",
    # --- thriller ---
    "thriller": "thriller",
    "triller": "thriller",        # uz, 35 — the single most common value
    "psixologik": "thriller",     # uz "psychological", i.e. psych thriller
    # --- war ---
    "war": "war",
    "urush": "war",
    # --- western ---
    "western": "western",
    "vestern": "western",
    # --- TMDB genres with no local variant yet ---
    "tv movie": "drama",
}

# Every canonical key must also map to itself, or normalising already-clean
# data would wipe it — that is what makes the migration re-runnable. Derived
# rather than hand-listed above: "science_fiction" was missed exactly once,
# because its human spelling ("science fiction") is the alias and the key
# only differs by an underscore. Generating this closes the whole class.
ALIASES: dict[str, str] = {**_VARIANTS, **{key: key for key in CANONICAL_GENRES}}

# Guards against a typo'd right-hand side quietly creating a genre that has
# no canonical entry and therefore no translation.
assert set(ALIASES.values()) <= set(CANONICAL_GENRES), (
    f"aliases point at unknown keys: {sorted(set(ALIASES.values()) - set(CANONICAL_GENRES))}"
)


def normalise_genre(value: str) -> str | None:
    """Canonical key for one raw genre value, or None if unrecognised."""
    if not value:
        return None
    return ALIASES.get(value.strip().lower())


def normalise_genres(values: list[str] | None) -> list[str]:
    """
    Canonical keys for a whole genres array: unrecognised values dropped,
    duplicates collapsed, original order kept.

    Order matters because the first genres are the ones the bot card and
    the Mini App banner show, and collapsing is not optional — "Ilmiy" and
    "Fantastika" on the same title both become science_fiction, and a
    duplicated key would count twice in the similarity score.
    """
    if not values:
        return []

    canonical: list[str] = []
    for value in values:
        key = normalise_genre(value)
        if key is not None and key not in canonical:
            canonical.append(key)
    return canonical
