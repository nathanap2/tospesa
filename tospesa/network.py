"""Réseau de notes et calcul du score personnel.

Une note est un couple (valeur, pondération) : la valeur dans [-1, 1], la
pondération dans ]0, 1] dit à quel point l'auteur engage sa note.

L'avis d'un auteur sur une entité est un couple (valeur, certitude) :
- s'il a noté l'entité, c'est la valeur de sa note, avec pour certitude sa
  pondération ;
- sinon, c'est la moyenne des valeurs de ses contacts de confiance (les
  auteurs qu'il note positivement) qui ont un avis, pondérée par sa confiance
  en chacun (valeur × pondération de sa note) et par leur certitude. Sa certitude vaut DAMPING fois la
  combinaison (« ou » probabiliste) des certitudes de ces contacts : elle
  baisse à chaque pas et monte avec le nombre de sources, sans dépasser 1.

Les notes négatives ne sont jamais traversées (règle de propagation). Le
calcul itère jusqu'à ce que les avis se stabilisent : la décroissance
géométrique de la certitude suffit à éteindre l'influence des chemins longs.

Le score d'un lecteur est la valeur de son propre avis, ou 0 s'il n'en a pas.
Pendant ce calcul, rien ne revient vers le lecteur : les liens qui pointent
vers lui sont ignorés, pour qu'il n'entende pas son propre avis en écho.
"""

from collections.abc import Iterator

DAMPING = 0.85
_TOLERANCE = 1e-9
_MAX_ITERATIONS = 10_000


class Network:
    def __init__(self):
        # author -> {target -> (value, weight)}
        self._notes: dict[str, dict[str, tuple[float, float]]] = {}

    def rate(
        self, author: str, target: str, value: float, weight: float = 1.0
    ) -> None:
        if not -1 <= value <= 1:
            raise ValueError(f"note hors de [-1, 1] : {value}")
        if not 0 < weight <= 1:
            raise ValueError(f"pondération hors de ]0, 1] : {weight}")
        if author == target:
            raise ValueError("un auteur ne se note pas lui-même")
        self._notes.setdefault(author, {})[target] = (value, weight)

    def unrate(self, author: str, target: str) -> None:
        del self._notes[author][target]
        if not self._notes[author]:
            del self._notes[author]

    def opinions(self, entity: str, viewer: str) -> dict[str, tuple[float, float]]:
        """Avis (valeur, certitude) sur l'entité de chaque auteur qui en a un,
        tels que les voit le lecteur."""
        direct = {
            author: notes[entity]
            for author, notes in self._notes.items()
            if entity in notes
        }
        opinions = dict(direct)
        for _ in range(_MAX_ITERATIONS):
            updated = dict(direct)
            for author, notes in self._notes.items():
                if author in direct:
                    continue
                weighted = weights = 0.0
                doubt = 1.0
                for contact, (trust, weight) in notes.items():
                    if trust > 0 and contact != viewer and contact in opinions:
                        trust *= weight
                        value, certainty = opinions[contact]
                        weighted += trust * certainty * value
                        weights += trust * certainty
                        doubt *= 1 - certainty
                if weights:
                    updated[author] = (weighted / weights, DAMPING * (1 - doubt))
            if _converged(opinions, updated):
                return updated
            opinions = updated
        raise RuntimeError(f"les avis sur {entity!r} ne convergent pas")

    def neighborhood(self, viewer: str) -> Iterator[tuple[str, int]]:
        """Entités notées positivement de proche en proche depuis le lecteur,
        avec leur distance en pas, des plus proches aux plus lointaines. Seuls
        les auteurs connus ont des contacts à explorer."""
        seen = {viewer}
        frontier = [viewer]
        distance = 0
        while frontier:
            distance += 1
            next_frontier = []
            for author in frontier:
                for contact, (trust, _) in self._notes.get(author, {}).items():
                    if trust > 0 and contact not in seen:
                        seen.add(contact)
                        next_frontier.append(contact)
                        yield contact, distance
            frontier = next_frontier

    def nearby_raters(self, viewer: str, entity: str) -> Iterator[tuple[str, int]]:
        """Auteurs du réseau de confiance du lecteur qui ont noté l'entité, avec
        leur distance en pas, du plus proche au plus lointain."""
        for author, distance in self.neighborhood(viewer):
            if entity in self._notes.get(author, {}):
                yield author, distance

    def score(self, viewer: str, entity: str) -> float:
        value, _ = self.opinions(entity, viewer).get(viewer, (0.0, 0.0))
        return value


def _converged(old: dict, new: dict) -> bool:
    return old.keys() == new.keys() and all(
        abs(old[a][0] - new[a][0]) < _TOLERANCE
        and abs(old[a][1] - new[a][1]) < _TOLERANCE
        for a in new
    )
