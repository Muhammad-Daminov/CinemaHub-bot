"""
Importing this package populates Base.metadata with every model —
in dependency order, so nothing here creates a cycle: user has no
dependency on the others; content depends only on Base; payment and
promo depend on enums/classes from user (already loaded by the time
we get to them below).

Anything that needs the full metadata (Alembic's env.py being the
main example) should `import app.db.models` explicitly rather than
relying on some other import to have triggered it as a side effect.
"""
from app.db.models import user  # noqa: F401
from app.db.models import content  # noqa: F401
from app.db.models import subscription  # noqa: F401
from app.db.models import payment  # noqa: F401
from app.db.models import promo  # noqa: F401
