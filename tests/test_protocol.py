"""Comportements du protocole : notes signées, empreintes, échange de notes."""

import json
from itertools import permutations

from tospesa.cli import main
from tospesa.protocol import Identity, entity_id
from tospesa.store import Store

BOOK = "Le Petit Prince"


def run(capsys, home, *args):
    main(["--home", str(home), *args])
    return capsys.readouterr().out.strip()


def score(capsys, home, entity):
    return float(run(capsys, home, "score", entity))


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
