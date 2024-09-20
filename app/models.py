# from datetime import datetime
import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db

class AnonymousSession(db.Model):  # type: ignore
    sessionUUID: so.Mapped[str] = so.mapped_column(sa.String(128), primary_key=True)
    musicEngine: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary(length=(2 ** 32) - 1))
    mei: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary(length=(2 ** 32) - 1))
    humdrum: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary(length=(2 ** 32) - 1))
    musicxml: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary(length=(2 ** 32) - 1))

    def __repr__(self):
        return f'<Anon {self.sessionUUID}>'

