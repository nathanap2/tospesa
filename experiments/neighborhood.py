"""Taille du voisinage utile pour la réplication locale.

Réseau synthétique « petit monde » (Watts-Strogatz orienté) : les
utilisateurs sont sur un anneau, chacun fait confiance à ses K voisins les
plus proches, et une fraction REWIRE de ces liens pointe vers un utilisateur
au hasard. Les avis sur une entité sont corrélés à la position sur l'anneau
(des voisins ont tendance à être d'accord).

Pour un lecteur et une entité, on compare le score complet au score calculé
avec les seules notes des auteurs à au plus h pas de confiance du lecteur.

Usage : uv run python experiments/neighborhood.py [N]
"""

import math
import random
import sys
from collections import deque

from tospesa.network import Network

K = 8
REWIRE = 0.1
ENTITIES = 2000
RATINGS_PER_USER = 5
ZIPF = 1.1
TOLERANCE = 0.05
MAX_RADIUS = 12


def build(n: int, rng: random.Random):
    notes: dict[str, dict[str, float]] = {}
    for u in range(n):
        contacts = notes.setdefault(f"u{u}", {})
        for d in range(1, K // 2 + 1):
            for v in ((u + d) % n, (u - d) % n):
                if rng.random() < REWIRE:
                    v = rng.randrange(n)
                if v != u:
                    contacts[f"u{v}"] = rng.uniform(0.3, 1)
    weights = [1 / (r + 1) ** ZIPF for r in range(ENTITIES)]
    phases = [rng.uniform(0, 2 * math.pi) for _ in range(ENTITIES)]
    for u in range(n):
        for e in rng.choices(range(ENTITIES), weights, k=RATINGS_PER_USER):
            affinity = math.cos(2 * math.pi * u / n + phases[e])
            notes[f"u{u}"][f"e{e}"] = 1.0 if affinity + rng.gauss(0, 0.7) > 0 else -1.0
    return notes


def network(notes, authors) -> Network:
    net = Network()
    for a in authors:
        for t, v in notes[a].items():
            net.rate(a, t, v)
    return net


def distances(notes, viewer: str) -> dict[str, int]:
    dist = {viewer: 0}
    queue = deque([viewer])
    while queue:
        a = queue.popleft()
        for t, v in notes.get(a, {}).items():
            if v > 0 and t in notes and t not in dist:
                dist[t] = dist[a] + 1
                queue.append(t)
    return dist


def main(n: int) -> None:
    rng = random.Random(0)
    notes = build(n, rng)
    raters: dict[str, int] = {}
    for contacts in notes.values():
        for t in contacts:
            if t.startswith("e"):
                raters[t] = raters.get(t, 0) + 1
    by_popularity = sorted(raters, key=raters.get, reverse=True)
    picks = {
        "populaire": by_popularity[0],
        "moyenne": by_popularity[len(by_popularity) // 10],
        "niche": by_popularity[len(by_popularity) // 2],
    }
    full = network(notes, notes)
    print(f"{n} utilisateurs, {K} contacts chacun, rewire {REWIRE}")
    for label, entity in picks.items():
        print(f"\nentité {label} ({entity}, notée par {raters[entity]})")
        for viewer in rng.sample(sorted(notes), 3):
            target = full.score(viewer, entity)
            dist = distances(notes, viewer)
            for h in range(MAX_RADIUS + 1):
                authors = [a for a, d in dist.items() if d <= h]
                approx = network(notes, authors).score(viewer, entity)
                if abs(approx - target) < TOLERANCE:
                    break
            else:
                h = None
            print(
                f"  {viewer:>6} : score {target:+.2f}, "
                f"rayon suffisant {h}, soit {len(authors)} auteurs "
                f"({100 * len(authors) / n:.1f} %)"
            )


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5000)
