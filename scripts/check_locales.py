"""
Locale parity check — the verification gate for app/locales/*.json.

Answers two questions CI cannot otherwise ask:

  1. Do uz/ru/en carry identical key sets? A key present in one and
     missing from another silently falls back to Uzbek for those users,
     which looks like a translation nobody got round to rather than the
     bug it is.
  2. Does every key referenced in code actually exist? A typo'd key
     renders as the literal key text in the UI ("app.row_top"), which is
     a visible bug report but only if someone happens to look at that
     screen in that language.

Run standalone (`python scripts/check_locales.py`) or via CI. Exits
non-zero on any failure so it can gate a build.

Dynamic keys — t(f"genre.{name}") and similar — cannot be resolved
statically. Their literal prefixes are listed in DYNAMIC_PREFIXES and
keys underneath them are exempt from the "unused" report, but never
from the parity check.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "app" / "locales"
LANGUAGES = ("uz", "ru", "en")
FALLBACK = "uz"

SEARCH_ROOTS = (ROOT / "app", ROOT / "webapp" / "src")
SEARCH_SUFFIXES = (".py", ".ts", ".tsx")

# Keys built at runtime from a variable suffix, e.g. t(f"genre.{g}").
# Everything under these prefixes is considered reachable.
# `payment.reject.*` resolves from a database row's `code`
# (RejectionReason.i18n_key) and `payment.status_*` from a status enum
# value, so neither is ever written as a literal.
DYNAMIC_PREFIXES = (
    "genre.", "audio.", "rank.", "common.lang_", "orders.kind_",
    "payment.reject.", "payment.status_", "badge.", "banner.label.",
    # Decoration names, resolved from the allowlist key the server stores.
    "theme.decoration.",
)

# The codebase reaches the catalogs through several call shapes, all of
# which have to be recognised or the "unused key" report is nonsense:
#
#   t("key", lang)                  module-level translator
#   _("key")                        the bot's per-update injected translator
#   PromoError("promo.expired")     exceptions that carry a key, not a message
#   t_for_user(session, id, "key")  key is the *third* argument
#
KEY_PATTERNS = (
    # t(...) / _(...) / anything ending in a translator-ish name, key first.
    re.compile(r"""(?:\bt|\b_|\bPromoError)\s*\(\s*['"`]([a-zA-Z0-9_.]+)['"`]"""),
    # t_for_user(session, user_id, "key") — skip two arguments to reach it.
    re.compile(r"""\bt_for_user\s*\([^,)]+,[^,)]+,\s*['"`]([a-zA-Z0-9_.]+)['"`]"""),
    # The LANGUAGES table in webapp/src/lib/i18n.ts uses labelKey: "common.lang_uz".
    re.compile(r"""labelKey:\s*['"`]([a-zA-Z0-9_.]+)['"`]"""),
)

# Any dotted string literal. Deliberately loose — feeds the advisory
# "unused key" report only, never the pass/fail check.
ANY_LITERAL = re.compile(r"""['"`]([a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)['"`]""")


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    """Locales are flat today, but tolerate nesting rather than silently skipping it."""
    out: dict[str, str] = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def load_catalogs() -> dict[str, dict[str, str]]:
    catalogs = {}
    for lang in LANGUAGES:
        path = LOCALES_DIR / f"{lang}.json"
        with path.open(encoding="utf-8") as handle:
            catalogs[lang] = flatten(json.load(handle))
    return catalogs


def collect_used_keys() -> tuple[dict[str, set[Path]], set[str]]:
    """
    Returns (called, literals).

    `called` holds keys matched by a recognised translator call shape and
    is what the build gates on — only these can reveal a typo, since a
    misspelled key still looks like a call.

    `literals` holds every string literal in the codebase, used *only* to
    decide whether a defined key is dead. That looser test is what keeps
    the advisory honest about shapes the call patterns cannot see —
    ternaries like t(ok ? "a.yes" : "a.no"), keys returned from a
    function, and f-string-built families.
    """
    called: dict[str, set[Path]] = {}
    literals: set[str] = set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SEARCH_SUFFIXES or "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in KEY_PATTERNS:
                for key in pattern.findall(text):
                    called.setdefault(key, set()).add(path.relative_to(ROOT))
            literals.update(ANY_LITERAL.findall(text))
    return called, literals


def main() -> int:
    catalogs = load_catalogs()
    failures: list[str] = []

    # 1. Parity between catalogs, measured against the union so that a key
    #    missing from every non-fallback locale is still reported.
    all_keys = set().union(*(set(c) for c in catalogs.values()))
    for lang in LANGUAGES:
        missing = sorted(all_keys - set(catalogs[lang]))
        if missing:
            failures.append(
                f"{lang}.json is missing {len(missing)} key(s) present in another locale:\n"
                + "\n".join(f"    {key}" for key in missing)
            )

    # 2. Every key used in code resolves in the fallback locale. Checking
    #    against the fallback is the real test: the backend merges each
    #    language over uz before sending, so a key missing from uz is
    #    missing everywhere.
    called, literals = collect_used_keys()
    unresolved = sorted(key for key in called if key not in catalogs[FALLBACK])
    if unresolved:
        failures.append(
            f"{len(unresolved)} key(s) used in code but absent from {FALLBACK}.json:\n"
            + "\n".join(
                f"    {key}  ({', '.join(str(p) for p in sorted(called[key]))})"
                for key in unresolved
            )
        )

    if failures:
        print("Locale check FAILED\n")
        for failure in failures:
            print(f"  - {failure}\n")
        return 1

    # Advisory only — an unused key is dead weight, not a broken build.
    unused = sorted(
        key
        for key in catalogs[FALLBACK]
        if key not in literals and not key.startswith(DYNAMIC_PREFIXES)
    )

    print(
        f"Locale check OK — {len(all_keys)} keys x {len(LANGUAGES)} languages, "
        f"{len(called)} resolved through translator calls."
    )
    if unused:
        print(f"\nNote: {len(unused)} key(s) defined but not referenced statically:")
        for key in unused:
            print(f"    {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
