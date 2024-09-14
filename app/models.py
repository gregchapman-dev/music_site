import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db

class AnonymousSession(db.Model):  # type: ignore
    sessionUUID: so.Mapped[str] = so.mapped_column(sa.String(128), primary_key=True)
    musicEngine: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary)
    mei: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary)
    humdrum: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary)
    musicxml: so.Mapped[bytes | None] = so.mapped_column(db.LargeBinary)

    def __repr__(self):
        return f'<Anon {self.sessionUUID}>'

