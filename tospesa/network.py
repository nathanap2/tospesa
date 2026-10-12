"""Réseau de notes et calcul du score personnel.

Une note est un couple (valeur, pondération) : la valeur dans [-1, 1], la
pondération dans ]0, 1] dit à quel point l'auteur engage sa note.

Le score se calcule comme une requête qui part du lecteur et descend dans
son réseau de confiance, en connaissant sa profondeur. Interrogé à une
profondeur donnée, un utilisateur répond (valeur, certitude, profondeur de sa
meilleure source) :
- s'il a noté l'entité : la valeur de sa note, avec pour certitude sa
  pondération ;
- sinon : la moyenne des réponses de ses contacts de confiance (interrogés
  un pas plus loin), pondérée par sa confiance en chacun (valeur × pondération
  de sa note) et par leur certitude. Sa certitude est celle de sa meilleure
  source, multipliée par la confiance qui y mène et atténuée de DAMPING à
  chaque pas.

On ne descend pas au-delà de MAX_DEPTH pas, là où l'atténuation passe sous
CUTOFF : une source aussi lointaine n'apporte plus d'information utile.

Les notes négatives ne sont jamais traversées. Pendant le calcul, on fait
comme si personne ne faisait confiance au lecteur : son propre avis ne peut
pas lui revenir déguisé en avis d'un autre. Les autres boucles s'éteignent
avec l'atténuation.

Le score d'un lecteur est la valeur de sa propre réponse, ou 0 s'il n'a
aucune information.
"""

import math
from collections.abc import Iterator
from typing import NamedTuple

DAMPING = 0.5
CUTOFF = 0.005
MAX_DEPTH = math.floor(math.log(CUTOFF) / math.log(DAMPING))


class Opinion(NamedTuple):
    value: float
    certainty: float
    depth: int  # distance de la meilleure source


NO_OPINION = Opinion(0.0, 0.0, 0)


class Network:
    def __init__(self):
        # author -> {target -> (value, weight)}
        self._notes: dict[str, dict[str, tuple[float, float]]] = {}
        # target -> auteurs qui lui font confiance (note positive)
        self._trusted_by: dict[str, set[str]] = {}
        # entité -> auteurs qui l'ont notée
        self._raters: dict[str, set[str]] = {}

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
        self._raters.setdefault(target, set()).add(author)
        trusted_by = self._trusted_by.setdefault(target, set())
        if value > 0:
            trusted_by.add(author)
        else:
            trusted_by.discard(author)

    def unrate(self, author: str, target: str) -> None:
        del self._notes[author][target]
        if not self._notes[author]:
            del self._notes[author]
        self._raters[target].discard(author)
        self._trusted_by.get(target, set()).discard(author)

    def opinion(self, viewer: str, entity: str) -> Opinion:
        """Réponse du lecteur sur l'entité ; NO_OPINION s'il n'a aucune
        information."""
        own = self._notes.get(viewer, {}).get(entity)
        if own is not None:
            return Opinion(own[0], own[1], 0)
        # Distance de chacun au notateur le plus proche, en remontant la
        # confiance depuis les notateurs, sans passer par le lecteur : on ne
        # descendra que vers ceux qui peuvent apporter une information à temps.
        to_source = {r: 0 for r in self._raters.get(entity, ()) if r != viewer}
        frontier = list(to_source)
        for distance in range(1, MAX_DEPTH):
            next_frontier = []
            for node in frontier:
                for author in self._trusted_by.get(node, ()):
                    if author != viewer and author not in to_source:
                        to_source[author] = distance
                        next_frontier.append(author)
            frontier = next_frontier

        memo: dict[tuple[str, int], Opinion | None] = {}

        def ask(user: str, depth: int) -> Opinion | None:
            key = (user, depth)
            if key in memo:
                return memo[key]
            notes = self._notes.get(user, {})
            if entity in notes:
                value, weight = notes[entity]
                answer = Opinion(value, weight, 0)
            else:
                weighted = weights = 0.0
                best = None
                for contact, (trust, weight) in notes.items():
                    if (
                        trust <= 0
                        or contact == viewer
                        or depth + 1 + to_source.get(contact, MAX_DEPTH) > MAX_DEPTH
                    ):
                        continue
                    reply = ask(contact, depth + 1)
                    if reply is None:
                        continue
                    strength = trust * weight * reply.certainty
                    weighted += strength * reply.value
                    weights += strength
                    if best is None or strength > best[0]:
                        best = (strength, reply.depth + 1)
                if best is None:
                    answer = None
                else:
                    answer = Opinion(weighted / weights, DAMPING * best[0], best[1])
            memo[key] = answer
            return answer

        return ask(viewer, 0) or NO_OPINION

    def score(self, viewer: str, entity: str) -> float:
        return self.opinion(viewer, entity).value

    def neighborhood(self, viewer: str) -> Iterator[tuple[str, int, str]]:
        """Entités notées positivement de proche en proche depuis le lecteur,
        des plus proches aux plus lointaines : (entité, distance en pas,
        utilisateur par lequel on l'a atteinte). Seuls les utilisateurs qui ont
        posé des notes ont des contacts à explorer."""
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
                        yield contact, distance, author
            frontier = next_frontier

    def nearby_raters(self, viewer: str, entity: str) -> Iterator[tuple[str, int]]:
        """Utilisateurs du réseau de confiance du lecteur qui ont noté
        l'entité, avec leur distance en pas, du plus proche au plus lointain."""
        for author, distance, _ in self.neighborhood(viewer):
            if entity in self._notes.get(author, {}):
                yield author, distance
