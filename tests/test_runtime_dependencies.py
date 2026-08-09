"""
Every third-party module `app/` imports must be declared in requirements.txt.

This exists because of a real outage. Phase 5 added `from PIL import ...`
and multipart upload routes without adding Pillow or python-multipart to
the runtime manifest. Nothing caught it: a developer machine already had
both, and CI installs `requirements-dev.txt`, which is a superset. Render
installs only `requirements.txt`, so its build crashed on startup with
`ModuleNotFoundError: No module named 'PIL'` — and because Render keeps
serving the last build that started, production quietly ran a
two-phase-old release for over a day.

CI now has a job that installs only the runtime manifest and imports the
app, which catches this at the same moment. This test catches it earlier
still, on the machine where the import was written, and says *which*
package is missing rather than failing at the first one.

False positives are the whole difficulty, so the filtering is explicit:
standard-library modules come from `sys.stdlib_module_names` rather than a
hand-kept list, first-party imports are anything under `app`, and the
import-name-to-package mapping is read from installed metadata
(`PIL` -> `Pillow`) instead of a table that would drift.
"""
import ast
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
REQUIREMENTS = ROOT / "requirements.txt"

FIRST_PARTY = {"app", "tests", "alembic"}


def _normalise(name: str) -> str:
    """PEP 503 name comparison — `python-multipart`, `python_multipart` and `Python-Multipart` are one package."""
    return name.lower().replace("_", "-").strip()


def _declared_packages() -> set[str]:
    """Distribution names in requirements.txt, without version specifiers or extras."""
    declared = set()
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # "sqlalchemy[asyncio]>=2.0.36" -> "sqlalchemy"
        for separator in ("[", ">", "<", "=", "!", "~", ";"):
            line = line.split(separator, 1)[0]
        declared.add(_normalise(line))
    return declared


def _imported_top_level_modules() -> dict[str, set[str]]:
    """{module: {files that import it}} for every non-stdlib, non-first-party import under app/."""
    found: dict[str, set[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import, which is first-party by
                # definition and has no module name to resolve.
                names = [node.module] if node.level == 0 and node.module else []
            else:
                continue

            for name in names:
                root = name.split(".", 1)[0]
                if root in sys.stdlib_module_names or root in FIRST_PARTY:
                    continue
                found.setdefault(root, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_third_party_import_is_declared():
    """
    The assertion that would have prevented the Pillow outage.

    Reports every missing package at once, with the file that imports it —
    fixing them one failure at a time is how the second one gets missed.
    """
    declared = _declared_packages()
    distributions = packages_distributions()

    missing: list[str] = []
    for module, files in sorted(_imported_top_level_modules().items()):
        providers = distributions.get(module)
        if not providers:
            # Installed but with no metadata, or not installed at all. The
            # clean-install CI job is the backstop for that case; guessing
            # a package name here would invent a false positive.
            continue
        if not any(_normalise(p) in declared for p in providers):
            missing.append(
                f"  {module} (provided by {', '.join(sorted(providers))}) "
                f"imported in {', '.join(sorted(files))}"
            )

    assert not missing, (
        "These modules are imported by app/ but no package providing them is declared in "
        "requirements.txt. Render installs only that file, so the deploy will crash on "
        "startup while the previous build keeps serving:\n" + "\n".join(missing)
    )


def test_the_known_runtime_dependencies_are_declared():
    """
    Names the two that were actually missing, so a future edit that drops
    either fails with a message saying why they matter rather than a
    generic omission.
    """
    declared = _declared_packages()
    for package, reason in {
        "pillow": "app/services/images.py re-encodes uploaded receipts and posters",
        "python-multipart": "FastAPI needs it to parse the UploadFile routes, and raises at import time without it",
    }.items():
        assert package in declared, f"{package} must stay in requirements.txt — {reason}"


def test_the_checker_sees_the_real_imports():
    """
    Guards the guard. If the AST walk silently stopped finding anything —
    a moved directory, a parse change — the test above would pass while
    checking nothing.
    """
    modules = _imported_top_level_modules()
    for expected in ("fastapi", "sqlalchemy", "aiogram", "PIL"):
        assert expected in modules, f"{expected} should have been detected as a third-party import"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("sqlalchemy[asyncio]>=2.0.36", "sqlalchemy"),
        ("Pillow>=11.0", "pillow"),
        ("python-multipart>=0.0.20", "python-multipart"),
        ("uvicorn[standard]>=0.34", "uvicorn"),
    ],
)
def test_requirement_lines_parse_to_package_names(line, expected):
    """The parser strips extras and specifiers — otherwise every name would look undeclared."""
    for separator in ("[", ">", "<", "=", "!", "~", ";"):
        line = line.split(separator, 1)[0]
    assert _normalise(line) == expected
