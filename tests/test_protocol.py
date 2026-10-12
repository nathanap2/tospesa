"""Comportements du protocole : notes signées, empreintes, échange de notes."""

import json
from itertools import permutations

from tospesa.cli import main
from tospesa.protocol import Identity, entity_id
from tospesa.store import Store

BOOK = "Le Petit Prince"
FILM = "Metropolis"


def run(capsys, home, *args):
    main(["--home", str(home), *args])
    return capsys.readouterr().out.strip()


def score(capsys, home, entity):
    """Le dernier score affiché : celui du réseau s'il a été interrogé."""
    return float(run(capsys, home, "score", entity).splitlines()[-1].split()[0])


def test_scores_flow_through_exchanged_notes(tmp_path, capsys):
    # Bob fait confiance à Alice. Tant qu'il n'a pas ses notes, le livre est
    # neutre pour lui ; une fois le fichier d'Alice importé, il hérite de son
    # avis. Le fichier échangé ne contient pas la chaîne notée.
    alice, bob = tmp_path / "alice", tmp_path / "bob"
    alice_key = run(capsys, alice, "init")
    run(capsys, bob, "init")
    run(capsys, alice, "rate", BOOK, "1")
    run(capsys, bob, "rate", alice_key, "1")
    assert score(capsys, bob, BOOK) == 0

    exported = tmp_path / "alice-notes.jsonl"
    run(capsys, alice, "export", str(exported))
    assert BOOK not in exported.read_text()
    run(capsys, bob, "import", str(exported))
    assert score(capsys, bob, BOOK) == 1


def test_forged_notes_are_rejected(tmp_path, capsys):
    # Une note modifiée après sa signature n'est pas acceptée.
    alice, bob = tmp_path / "alice", tmp_path / "bob"
    alice_key = run(capsys, alice, "init")
    run(capsys, bob, "init")
    run(capsys, alice, "rate", BOOK, "1")
    run(capsys, bob, "rate", alice_key, "1")

    exported = tmp_path / "alice-notes.jsonl"
    run(capsys, alice, "export", str(exported))
    forged = []
    for line in exported.read_text().splitlines():
        note = json.loads(line)
        if note["target"] == entity_id(BOOK):
            note["value"] = -1
        forged.append(json.dumps(note))
    exported.write_text("\n".join(forged) + "\n")

    run(capsys, bob, "import", str(exported))
    assert score(capsys, bob, BOOK) == 0


def test_merging_notes_is_order_independent():
    # Les notes peuvent arriver dans n'importe quel ordre, en double, par
    # plusieurs pairs : l'état final est toujours le même, la plus récente
    # note de chaque auteur sur chaque entité l'emporte, retrait compris.
    alice, bob = Identity.generate(), Identity.generate()
    trust = bob.sign_note(alice.id, 1, 1, timestamp=1)
    rated = alice.sign_note(entity_id(BOOK), 1, 1, timestamp=1)
    changed = alice.sign_note(entity_id(BOOK), -1, 1, timestamp=2)
    retracted = alice.sign_note(entity_id(BOOK), None, 1, timestamp=3)

    for order in permutations([trust, rated, changed, changed]):
        store = Store()
        for note in order:
            store.add(note)
        assert store.network().score(bob.id, entity_id(BOOK)) == -1

    for order in permutations([trust, rated, changed, retracted]):
        store = Store()
        for note in order:
            store.add(note)
        assert store.network().score(bob.id, entity_id(BOOK)) == 0


def test_sync_replicates_the_trust_neighborhood():
    # Bob ne fait confiance qu'à Alice. En se synchronisant avec un pair, il
    # récupère les notes d'Alice, puis celles des gens en qui Alice a
    # confiance, mais jamais celles d'un inconnu. Un second passage n'apporte
    # plus rien.
    alice, bob, carol, mallory = (Identity.generate() for _ in range(4))
    peer = Store()
    for note in [
        alice.sign_note(entity_id(BOOK), 1, timestamp=1),
        alice.sign_note(carol.id, 1, timestamp=1),
        carol.sign_note(entity_id(FILM), -1, timestamp=1),
        mallory.sign_note(entity_id(BOOK), -1, timestamp=1),
    ]:
        peer.add(note)
    mine = Store()
    mine.add(bob.sign_note(alice.id, 1, timestamp=1))

    assert mine.pull(peer, bob.id) == 3
    assert {note.author for note in mine} == {bob.public, alice.public, carol.public}
    assert mine.network().score(bob.id, entity_id(BOOK)) == 1
    assert mine.network().score(bob.id, entity_id(FILM)) == -1
    assert mine.pull(peer, bob.id) == 0


def test_sync_sends_closest_authors_first():
    # Avec une connexion qui ne laisse passer qu'une note, Bob reçoit celle
    # d'Alice (à 1 pas) plutôt que celle de Carol (à 2 pas).
    alice, bob, carol = (Identity.generate() for _ in range(3))
    trust_carol = alice.sign_note(carol.id, 1, timestamp=1)
    peer = Store()
    for note in [
        trust_carol,
        carol.sign_note(entity_id(FILM), -1, timestamp=2),
        alice.sign_note(entity_id(BOOK), 1, timestamp=2),
    ]:
        peer.add(note)
    mine = Store()
    mine.add(bob.sign_note(alice.id, 1, timestamp=1))
    mine.add(trust_carol)

    assert mine.pull(peer, bob.id, limit=1) == 1
    assert mine.network().score(bob.id, entity_id(BOOK)) == 1
    assert mine.network().score(bob.id, entity_id(FILM)) == 0
