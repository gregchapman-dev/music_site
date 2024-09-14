import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db

class AnonymousSession(sa.Model):  # type: ignore
    sessionUUID: so.Mapped[str] = so.mapped_column(sa.String(128), primary_key=True)
    sessionState: so.Mapped[bytes] = so.mapped_column(db.LargeBinary)

    def __repr__(self):
        return f'<Anon {self.sessionUUID}>'

