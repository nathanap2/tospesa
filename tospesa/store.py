"""Copie locale des notes connues.

La fusion ne dépend ni de l'ordre d'arrivée des notes, ni de leurs doublons :
pour chaque (auteur, entité), la note la plus récente l'emporte, la signature
départageant deux notes de même date. Deux copies qui ont reçu les mêmes
notes, par n'importe quels chemins, sont donc identiques.

La synchronisation fonctionne au mieux, par dates : on demande à un pair les
notes plus récentes que celles qu'on a déjà, pour les auteurs de son
voisinage de confiance, les plus proches d'abord. Les notes d'inconnus ne
sont jamais copiées.
"""

from collections.abc import Iterable, Iterator
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

    def wants(self, viewer: str) -> list[tuple[str, int]]:
        """(auteur, date de sa note la plus récente connue) pour le lecteur et
        son voisinage de confiance, du plus proche au plus lointain."""
        heads: dict[str, int] = {}
        for note in self:
            heads[note.author_id] = max(heads.get(note.author_id, -1), note.timestamp)
        authors = [viewer, *(a for a, _ in self.network().neighborhood(viewer))]
        return [(author, heads.get(author, -1)) for author in authors]

    def notes_for(
        self, wants: Iterable[tuple[str, int]], limit: int | None = None
    ) -> list[Note]:
        """Notes plus récentes que les dates demandées, dans l'ordre demandé."""
        by_author: dict[str, list[Note]] = {}
        for note in self:
            by_author.setdefault(note.author_id, []).append(note)
        sent: list[Note] = []
        for author, since in wants:
            for note in sorted(by_author.get(author, []), key=lambda n: n.timestamp):
                if note.timestamp > since:
                    if len(sent) == limit:
                        return sent
                    sent.append(note)
        return sent

    def pull(self, peer: "Store", viewer: str, limit: int | None = None) -> int:
        """Récupère auprès du pair les notes du voisinage de confiance du
        lecteur, en élargissant le voisinage à mesure que les notes arrivent.
        Renvoie le nombre de notes nouvelles."""
        added = 0
        while limit is None or added < limit:
            remaining = None if limit is None else limit - added
            received = sum(
                self.add(note) for note in peer.notes_for(self.wants(viewer), remaining)
            )
            if not received:
                break
            added += received
        return added

    def save(self, path: Path) -> None:
        path.write_text("".join(note.to_json() + "\n" for note in self))

    @classmethod
    def load(cls, path: Path) -> "Store":
        store = cls()
        if path.exists():
            for line in path.read_text().splitlines():
                store.add(Note.from_json(line))
        return store
