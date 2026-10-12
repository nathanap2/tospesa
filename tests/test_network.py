"""Comportements du réseau : plusieurs nœuds qui tournent pour de vrai."""

import pytest

from tospesa.node import Home, Node, sync
from tospesa.protocol import entity_id, format_address, parse_address
from test_protocol import BOOK, FILM, run

RARE = "Un article obscur"
OTHER = "Un autre article"


@pytest.fixture
def start(tmp_path):
    """Démarre le nœud d'un dossier (ou un relais) ; tout est arrêté à la fin."""
    running = []

    def start(name, relay=False):
        home = Home(tmp_path / name)
        if relay:
            home.create_identity()
        node = Node(home, relay=relay).start()
        running.append(node)
        return node

    yield start
    for node in running:
        node.stop()


@pytest.fixture
def user(tmp_path, capsys):
    """Crée un utilisateur et renvoie (dossier, clé publique)."""

    def user(name):
        home = tmp_path / name
        return home, run(capsys, home, "init")

    return user


def local_score(capsys, home, entity):
    return float(run(capsys, home, "score", "--local", entity).split()[0])


def authors(home):
    return {record.author for record in Home(home).store}


def test_notes_travel_through_a_relay(capsys, start, user):
    # Alice et Mallory publient leur avis sur le livre via un relais. Bob ne
    # fait confiance qu'à Alice : après synchronisation, il a son avis à elle,
    # et celui de Mallory, une inconnue, ne compte pas.
    relay = start("relay", relay=True)
    (alice, alice_key), (bob, _), (mallory, _) = user("alice"), user("bob"), user("mallory")
    run(capsys, alice, "rate", BOOK, "1")
    run(capsys, mallory, "rate", BOOK, "-1")
    run(capsys, bob, "rate", alice_key, "1")
    for home in (alice, bob, mallory):
        run(capsys, home, "peers", "add", relay.address)

    run(capsys, alice, "sync")
    run(capsys, mallory, "sync")
    assert local_score(capsys, bob, BOOK) == 0
    run(capsys, bob, "sync")
    assert local_score(capsys, bob, BOOK) == 1


def test_contacts_keep_backup_copies(capsys, start, user):
    # Carol s'est synchronisée avec le nœud d'Alice, puis Alice s'est
    # déconnectée. Bob, qui fait confiance à Alice et ne connaît que le nœud
    # de Carol, récupère quand même les notes d'Alice. Il apprend au passage
    # l'adresse d'Alice, essaie de la joindre et passe outre son absence.
    (alice, alice_key), (bob, _), (carol, _) = user("alice"), user("bob"), user("carol")
    alice_node = start("alice")
    run(capsys, alice, "rate", BOOK, "1")  # pendant que son nœud tourne
    run(capsys, carol, "rate", alice_key, "1")
    run(capsys, carol, "peers", "add", alice_node.address)
    run(capsys, carol, "sync")
    carol_node = start("carol")
    alice_node.stop()

    run(capsys, bob, "rate", alice_key, "1")
    run(capsys, bob, "peers", "add", carol_node.address)
    report = sync(Home(bob))
    assert carol_node.address in report.reached
    assert alice_node.address in report.unreachable
    assert local_score(capsys, bob, BOOK) == 1


def test_announces_lead_to_contacts_nodes(capsys, start, user):
    # Le relais n'a qu'une partie des notes d'Alice : elle a noté le film après
    # sa dernière synchronisation. Bob trouve l'adresse du nœud d'Alice dans
    # son annonce, sur le relais, et y récupère la note manquante.
    relay = start("relay", relay=True)
    (alice, alice_key), (bob, _) = user("alice"), user("bob")
    alice_node = start("alice")
    run(capsys, alice, "peers", "add", relay.address)
    run(capsys, alice, "rate", BOOK, "1")
    run(capsys, alice, "sync")
    run(capsys, alice, "rate", FILM, "-1")

    run(capsys, bob, "rate", alice_key, "1")
    run(capsys, bob, "peers", "add", relay.address)
    report = sync(Home(bob))
    assert set(report.reached) == {relay.address, alice_node.address}
    assert local_score(capsys, bob, BOOK) == 1
    assert local_score(capsys, bob, FILM) == -1


def test_personal_node_only_accepts_its_trust_neighborhood(capsys, start, user):
    # Le nœud de Bob accepte les notes que lui publie Alice, en qui il a
    # confiance, et refuse celles de Mallory, une inconnue.
    (alice, alice_key), (bob, _), (mallory, mallory_key) = (
        user("alice"), user("bob"), user("mallory")
    )
    run(capsys, bob, "rate", alice_key, "1")
    bob_node = start("bob")
    for home, value in ((alice, "1"), (mallory, "-1")):
        run(capsys, home, "rate", BOOK, value)
        run(capsys, home, "peers", "add", bob_node.address)
        run(capsys, home, "sync")

    assert alice_key in authors(bob)
    assert mallory_key not in authors(bob)
    assert local_score(capsys, bob, BOOK) == 1


def test_rare_entity_found_by_targeted_search(capsys, start, user):
    # Seul Dan, à trois pas de Bob, a noté l'article. Bob ne s'est jamais
    # synchronisé : sa copie locale ne sait rien, mais en consultant
    # l'article, il demande au relais qui, dans son réseau, l'a noté. Il
    # récupère la chaîne de confiance jusqu'à Dan, et rien d'autre.
    relay = start("relay", relay=True)
    (alice, alice_key), (bob, _), (carol, carol_key), (dan, dan_key) = (
        user(name) for name in ("alice", "bob", "carol", "dan")
    )
    run(capsys, alice, "rate", carol_key, "1")
    run(capsys, carol, "rate", dan_key, "1")
    run(capsys, dan, "rate", RARE, "1")
    run(capsys, dan, "rate", OTHER, "-1")
    for home in (alice, carol, dan):
        run(capsys, home, "peers", "add", relay.address)
        run(capsys, home, "sync")

    run(capsys, bob, "rate", alice_key, "1")
    run(capsys, bob, "peers", "add", relay.address)
    shown = run(capsys, bob, "score", RARE).splitlines()
    assert [float(line.split()[0]) for line in shown] == [0, 1]
    store = Home(bob).store
    assert store.note(entity_id(dan_key), entity_id(RARE)) is not None
    assert store.note(entity_id(dan_key), entity_id(OTHER)) is None


def test_impostor_nodes_are_detected(capsys, start, user):
    # Une annonce ou une configuration peut pointer vers une machine qui ne
    # détient pas la clé de l'adresse (usurpation, ou simple changement de
    # main). Bob la détecte avant de lui envoyer quoi que ce soit.
    (alice, alice_key), (bob, _), (carol, _) = user("alice"), user("bob"), user("carol")
    run(capsys, alice, "rate", BOOK, "1")
    carol_node = start("carol")
    _, host, port = parse_address(carol_node.address)
    fake_alice = format_address(alice_key, host, port)

    run(capsys, bob, "rate", alice_key, "1")
    run(capsys, bob, "rate", FILM, "1")
    run(capsys, bob, "peers", "add", fake_alice)
    report = sync(Home(bob))
    assert report.impostors == [fake_alice]
    assert report.reached == []
    assert authors(carol) == {run(capsys, carol, "id")}
