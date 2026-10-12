"""Copie locale des notes connues.

La fusion ne dépend ni de l'ordre d'arrivée des notes, ni de leurs doublons :
pour chaque (auteur, entité), la note la plus récente l'emporte, la signature
départageant deux notes de même date. Deux copies qui ont reçu les mêmes
notes, par n'importe quels chemins, sont donc identiques.
"""

from collections.abc import Iterator
from pathlib import Path

from tospesa.network import Network
from tospesa.protocol import Note


class Store:
    def __init__(self):
        self._latest: dict[tuple[str, str], Note] = {}

    def add(self, note: Note) -> bool:
        """Vérifie et intègre la note. Renvoie True si elle change l'état."""
        note.verify()
        key = (note.author, note.target)
        current = self._latest.get(key)
        if current and (current.timestamp, current.signature) >= (
            note.timestamp,
            note.signature,
        ):
            return False
        self._latest[key] = note
        return True

    def latest(self, author: str, target: str) -> Note | None:
        return self._latest.get((author, target))

    def __iter__(self) -> Iterator[Note]:
        return iter(self._latest.values())

    def network(self) -> Network:
        net = Network()
        for note in self:
            if note.value is not None:
                net.rate(note.author_id, note.target, note.value, note.weight)
        return net

    def save(self, path: Path) -> None:
        path.write_text("".join(note.to_json() + "\n" for note in self))

    @classmethod
    def load(cls, path: Path) -> "Store":
        store = cls()
        if path.exists():
            for line in path.read_text().splitlines():
                store.add(Note.from_json(line))
        return store
