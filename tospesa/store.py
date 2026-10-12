"""Copie locale des enregistrements connus (notes et annonces).

La fusion ne dépend ni de l'ordre d'arrivée, ni des doublons : pour chaque
(auteur, entité), la note la plus récente l'emporte, et pour chaque auteur,
l'annonce la plus récente ; la signature départage deux enregistrements de
même date. Deux copies qui ont reçu les mêmes enregistrements, par n'importe
quels chemins, sont donc identiques.

Une copie peut être attachée à un fichier : un journal où l'on ne fait
qu'ajouter, partagé par plusieurs processus (le nœud qui tourne en continu,
la ligne de commande). Chacun y ajoute sous verrou et relit ce que les autres
ont ajouté (`refresh`).

La synchronisation fonctionne au mieux, par dates : on demande à un pair les
enregistrements plus récents que ceux qu'on a déjà, pour soi et son
voisinage de confiance, les plus proches d'abord. Ceux des inconnus ne sont
jamais copiés.
"""

import fcntl
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from tospesa.network import Network
from tospesa.protocol import Announce, Note, Record, record_from_json

# (auteur, date de sa note la plus récente connue, date de son annonce)
Want = tuple[str, int, int]


class Peer(Protocol):
    """Ce qu'on attend d'un pair : une copie locale, un fichier, un nœud distant."""

    def records_for(
        self, wants: Iterable[Want], limit: int | None = None
    ) -> list[Record]: ...


class Store:
    def __init__(self, path: Path | None = None):
        self._notes: dict[tuple[str, str], Note] = {}
        self._announces: dict[str, Announce] = {}
        self._path = path
        self._offset = 0
        self._inode: int | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.refresh()

    # --- état -------------------------------------------------------------

    def add(self, record: Record) -> bool:
        """Vérifie et intègre l'enregistrement, et l'ajoute au journal s'il
        change l'état. Renvoie True dans ce cas."""
        record.verify()
        if not self._integrate(record):
            return False
        if self._path is not None:
            with self._locked(fcntl.LOCK_EX), open(self._path, "a") as journal:
                journal.write(record.to_json() + "\n")
        return True

    def _integrate(self, record: Record) -> bool:
        if isinstance(record, Note):
            table, key = self._notes, (record.author_id, record.target)
        else:
            table, key = self._announces, record.author_id
        current = table.get(key)
        if current and (current.timestamp, current.signature) >= (
            record.timestamp,
            record.signature,
        ):
            return False
        table[key] = record
        return True

    def note(self, author_id: str, target: str) -> Note | None:
        return self._notes.get((author_id, target))

    def announce(self, author_id: str) -> Announce | None:
        return self._announces.get(author_id)

    def notes(self) -> Iterator[Note]:
        return iter(self._notes.values())

    def __iter__(self) -> Iterator[Record]:
        yield from self._notes.values()
        yield from self._announces.values()

    def network(self) -> Network:
        net = Network()
        for note in self.notes():
            if note.value is not None:
                net.rate(note.author_id, note.target, note.value, note.weight)
        return net

    # --- synchronisation --------------------------------------------------

    def heads(self, authors: Iterable[str]) -> list[Want]:
        """Dates des enregistrements les plus récents connus pour ces auteurs
        (-1 si aucun)."""
        latest: dict[str, int] = {}
        for note in self.notes():
            latest[note.author_id] = max(latest.get(note.author_id, -1), note.timestamp)
        wants = []
        for author in authors:
            announce = self._announces.get(author)
            wants.append(
                (author, latest.get(author, -1), announce.timestamp if announce else -1)
            )
        return wants

    def wants(self, viewer: str) -> list[Want]:
        """Ce qu'on demande à un pair : le lecteur et son voisinage de
        confiance, du plus proche au plus lointain."""
        neighborhood = (entity for entity, _, _ in self.network().neighborhood(viewer))
        return self.heads([viewer, *neighborhood])

    def records_for(
        self, wants: Iterable[Want], limit: int | None = None
    ) -> list[Record]:
        """Enregistrements plus récents que les dates demandées, dans l'ordre
        demandé : pour chaque auteur, son annonce puis ses notes."""
        by_author: dict[str, list[Note]] = {}
        for note in self.notes():
            by_author.setdefault(note.author_id, []).append(note)
        sent: list[Record] = []
        for author, notes_since, announce_since in wants:
            candidates: list[Record] = []
            announce = self._announces.get(author)
            if announce and announce.timestamp > announce_since:
                candidates.append(announce)
            candidates += [
                note
                for note in sorted(by_author.get(author, []), key=lambda n: n.timestamp)
                if note.timestamp > notes_since
            ]
            for record in candidates:
                if len(sent) == limit:
                    return sent
                sent.append(record)
        return sent

    def pull(self, peer: Peer, viewer: str, limit: int | None = None) -> int:
        """Récupère auprès du pair les enregistrements du voisinage de confiance
        du lecteur, en élargissant le voisinage à mesure qu'ils arrivent. Ce
        que le pair renvoie sans qu'on l'ait demandé est ignoré. Renvoie le
        nombre d'enregistrements nouveaux."""
        added = 0
        while limit is None or added < limit:
            remaining = None if limit is None else limit - added
            wants = self.wants(viewer)
            wanted = {author for author, _, _ in wants}
            received = sum(
                self.add(record)
                for record in peer.records_for(wants, remaining)
                if record.author_id in wanted
            )
            if not received:
                break
            added += received
        return added

    def trace(self, viewer: str, entity: str, limit: int | None = None) -> list[Note]:
        """Recherche ciblée : les auteurs du réseau de confiance du lecteur qui
        ont noté l'entité, les plus proches d'abord, avec pour chacun la chaîne
        de notes qui le relie au lecteur. Tout ce qui est renvoyé appartient
        donc au voisinage de confiance du lecteur."""
        via: dict[str, str] = {}
        found: dict[tuple[str, str], Note] = {}
        raters = 0
        for node, _, parent in self.network().neighborhood(viewer):
            via[node] = parent
            rating = self._notes.get((node, entity))
            if rating is None or rating.value is None:
                continue
            found[(node, entity)] = rating
            while node != viewer:
                parent = via[node]
                found[(parent, node)] = self._notes[(parent, node)]
                node = parent
            raters += 1
            if raters == limit:
                break
        return list(found.values())

    # --- persistance ------------------------------------------------------

    @contextmanager
    def _locked(self, mode: int):
        with open(self._path.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, mode)
            yield

    def refresh(self) -> None:
        """Intègre ce que d'autres processus ont ajouté au journal."""
        if self._path is None:
            return
        with self._locked(fcntl.LOCK_SH):
            self._read_journal()

    def _read_journal(self) -> None:
        if not self._path.exists():
            return
        with open(self._path, "rb") as journal:
            status = os.fstat(journal.fileno())
            if status.st_ino != self._inode or status.st_size < self._offset:
                self._inode, self._offset = status.st_ino, 0
            journal.seek(self._offset)
            data = journal.read()
        complete = data[: data.rfind(b"\n") + 1]
        self._offset += len(complete)
        for line in complete.decode().splitlines():
            record = record_from_json(line)
            record.verify()
            self._integrate(record)

    def compact(self) -> None:
        """Réécrit le journal sans les enregistrements remplacés."""
        with self._locked(fcntl.LOCK_EX):
            self._read_journal()
            temporary = self._path.with_suffix(".tmp")
            self.save(temporary)
            temporary.replace(self._path)
            status = self._path.stat()
            self._inode, self._offset = status.st_ino, status.st_size

    def save(self, path: Path) -> None:
        """Écrit un instantané de la copie (pour l'export)."""
        path.write_text("".join(record.to_json() + "\n" for record in self))
