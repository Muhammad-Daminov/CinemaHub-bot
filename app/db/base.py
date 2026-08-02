"""
Declarative Base for models OWNED by CinemaHub Pro.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """All new ORM models must inherit from this — nothing else."""
    pass
