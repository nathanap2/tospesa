"""Comportements du réseau : plusieurs nœuds qui tournent pour de vrai."""

import re
import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tospesa import upnp

from tospesa.node import Home, Node, RemoteNode, sync
from tospesa.protocol import entity_id, format_address, parse_address
from test_protocol import BOOK, FILM, run, score

RARE = "Un article obscur"
OTHER = "Un autre article"


@pytest.fixture
def start(tmp_path):
    """Démarre le nœud d'un dossier (ou un relais) ; tout est arrêté à la fin."""
    running = []

    def start(name, relay=False, **options):
        home = Home(tmp_path / name)
        if relay:
            home.create_identity()
        node = Node(home, relay=relay, **options).start()
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
    # et celui de Mallory, une inconnue, ne compte pas. Le relais n'a reçu
    # que l'empreinte du livre, jamais son titre.
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
    assert BOOK not in (relay.home.path / "records.jsonl").read_text()


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


def test_forged_notes_are_rejected(capsys, start, user):
    # Une note d'Alice modifiée après sa signature (le livre passe de 1 à -1)
    # est rejetée par le nœud de Bob, bien qu'il fasse confiance à Alice.
    (alice, alice_key), (bob, _) = user("alice"), user("bob")
    run(capsys, bob, "rate", alice_key, "1")
    bob_node = start("bob")
    run(capsys, alice, "rate", BOOK, "1")
    note = Home(alice).store.note(entity_id(alice_key), entity_id(BOOK))

    reply = RemoteNode(bob_node.address).push([replace(note, value=-1)])
    assert reply["rejected"] == 1
    assert local_score(capsys, bob, BOOK) == 0


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


class FakeBox:
    """Une box qui parle UPnP (SSDP puis SOAP), joignable en unicast sur
    127.0.0.1, et qui donne 127.0.0.1 comme adresse publique."""

    def __init__(self):
        self.mappings: dict[int, str] = {}
        box = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._reply(
                    '<root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList>'
                    "<service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1"
                    "</serviceType><controlURL>/ctl</controlURL></service>"
                    "</serviceList></device></root>"
                )

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                action = self.headers["SOAPAction"].strip('"').split("#")[1]
                field = lambda name: re.search(f"<{name}>(.*?)</{name}>", body)[1]  # noqa: E731
                if action == "AddPortMapping":
                    box.mappings[int(field("NewExternalPort"))] = field("NewInternalClient")
                elif action == "DeletePortMapping":
                    box.mappings.pop(int(field("NewExternalPort")))
                answer = (
                    "<NewExternalIPAddress>127.0.0.1</NewExternalIPAddress>"
                    if action == "GetExternalIPAddress" else ""
                )
                self._reply(
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="x">{answer}</u:{action}Response>'
                    "</s:Body></s:Envelope>"
                )

            def _reply(self, text):
                data = text.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.ssdp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ssdp.bind(("127.0.0.1", 0))
        location = f"http://127.0.0.1:{self.http.server_port}/desc.xml"

        def answer_searches():
            while True:
                try:
                    _, sender = self.ssdp.recvfrom(4096)
                    self.ssdp.sendto(f"HTTP/1.1 200 OK\r\nLOCATION: {location}\r\n\r\n".encode(), sender)
                except OSError:
                    return

        threading.Thread(target=answer_searches, daemon=True).start()

    def discover(self) -> upnp.Gateway:
        return upnp.discover(timeout=0.3, target=self.ssdp.getsockname())

    def close(self):
        self.http.shutdown()
        self.ssdp.close()


def test_node_behind_a_box_opens_a_port_and_gets_verified(capsys, start, user):
    # Alice écoute sur toutes les interfaces, derrière une box UPnP. Son nœud
    # demande à la box d'ouvrir un port, en déduit son adresse publique, la
    # fait vérifier par le relais (qui la rappelle), puis l'annonce. Bob, qui
    # ne connaît que le relais, finit par joindre directement le nœud d'Alice.
    # Une adresse IPv6 détectée mais injoignable n'est jamais annoncée.
    box = FakeBox()
    relay = start("relay", relay=True)
    (alice, alice_key), (bob, _) = user("alice"), user("bob")
    alice_node = Node(Home(alice), host="::", find_gateway=box.discover,
                      find_ipv6=lambda: "2001:db8::1").start()
    public = format_address(alice_key, "127.0.0.1", alice_node.port)
    unreachable = format_address(alice_key, "2001:db8::1", alice_node.port)
    assert box.mappings == {alice_node.port: "127.0.0.1"}
    assert Home(alice).config()["candidates"] == [public, unreachable]

    run(capsys, alice, "peers", "add", relay.address)
    run(capsys, alice, "rate", BOOK, "1")
    sync(Home(alice))
    assert Home(alice).config()["addresses"] == [public]
    assert Home(alice).config()["candidates"] == [unreachable]
    run(capsys, alice, "rate", FILM, "-1")  # seulement sur son nœud

    run(capsys, bob, "rate", alice_key, "1")
    run(capsys, bob, "peers", "add", relay.address)
    assert public in sync(Home(bob)).reached
    assert local_score(capsys, bob, FILM) == -1

    alice_node.stop()
    assert box.mappings == {}
    box.close()


def test_friends_join_each_other_with_invitations(capsys, start, tmp_path):
    # Alice fait tourner son nœud et envoie une invitation à Bob, qui l'accepte
    # et lui renvoie la sienne. Sans rien configurer d'autre, chacun voit
    # ensuite l'avis de l'autre, et désigne l'autre par son nom.
    alice, bob = tmp_path / "alice", tmp_path / "bob"
    run(capsys, alice, "init")
    run(capsys, bob, "init")
    start("alice")
    run(capsys, bob, "join", run(capsys, alice, "invite"), "Alice")
    run(capsys, alice, "join", run(capsys, bob, "invite"), "Bob")

    run(capsys, alice, "rate", BOOK, "1")
    assert score(capsys, bob, BOOK) == 1  # Bob interroge le nœud d'Alice

    run(capsys, bob, "rate", FILM, "-1")
    run(capsys, bob, "sync")  # publie sur le nœud d'Alice, qui fait confiance à Bob
    assert local_score(capsys, alice, FILM) == -1

    run(capsys, bob, "rate", "@Alice", "0.5")
    assert Home(bob).store.note(entity_id(run(capsys, bob, "id")),
                                entity_id(run(capsys, alice, "id"))).value == 0.5


def test_spellings_of_one_url_designate_one_entity(capsys, user):
    (home, _) = user("alice")
    run(capsys, home, "rate", "https://Example.com:443/Page/#section", "1")
    assert local_score(capsys, home, "https://example.com/Page") == 1
    assert local_score(capsys, home, "https://example.com/page") == 0  # le chemin compte
