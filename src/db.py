import os
import sqlite3

PANEL_ENTRIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS panel_entries (
    section TEXT,
    name TEXT,
    PRIMARY KEY (section, name)
)
"""


class PanelEntries:
    """The catalog entries the panel made, which are the only ones it may remove."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(PANEL_ENTRIES_SCHEMA)
        self._conn.commit()

    def mark(self, section, name):
        self._conn.execute(
            "INSERT OR IGNORE INTO panel_entries (section, name) VALUES (?, ?)",
            (section, name),
        )
        self._conn.commit()

    def unmark(self, section, name):
        self._conn.execute(
            "DELETE FROM panel_entries WHERE section=? AND name=?", (section, name)
        )
        self._conn.commit()

    def names(self, section):
        rows = self._conn.execute(
            "SELECT name FROM panel_entries WHERE section=?", (section,)
        ).fetchall()
        return {r[0] for r in rows}
