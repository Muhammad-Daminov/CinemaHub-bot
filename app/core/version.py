"""
The application's release version.

**The root `VERSION` file is the single source of truth.** Both halves of
this project read that one file — Python here, and `vite.config.ts` at
build time for the Mini App — so the number in Settings and the number in
`/health` cannot disagree. That is the whole point: a frontend claiming
1.2.0 while the backend claims 1.1.4 tells you nothing about what is
actually deployed.

Plain text rather than `pyproject.toml` or `package.json` because neither
language should have to parse the other's manifest to learn the version,
and because the file belongs to the project rather than to either half.

Read once at import. The value is fixed for the life of the process — it
describes the build, and a build does not change while it runs.
"""
from pathlib import Path

# app/core/version.py -> app/core -> app -> repository root
_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"

# A deployment that cannot read its own version should still serve traffic:
# this is metadata, not a dependency. "unknown" is reported rather than
# raising, which would turn a missing file into a failed boot.
try:
    APP_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
except OSError:  # pragma: no cover — the file is committed alongside the code
    APP_VERSION = "unknown"
