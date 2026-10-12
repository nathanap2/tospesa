"""Nœuds du réseau.

Un nœud sert sa copie locale en HTTPS. Un nœud personnel appartient à un
utilisateur : il n'accepte que les enregistrements de son voisinage de
confiance, et comme sa copie contient ce voisinage, ses contacts peuvent y
retrouver les notes de tiers absents. Un relais est un nœud toujours en ligne
qui accepte tout enregistrement correctement signé : un facilitateur de
réseau, pas un juge. C'est le même programme, seule la politique d'accueil
diffère.

  POST /heads {"authors": [...]}                    -> {"heads": [want, ...]}
  POST /push  {"records": [...]}                    -> {"added", "refused", "rejected"}
  POST /pull  {"wants": [want, ...], "limit": n}    -> {"records": [...]}
  POST /trace {"viewer", "entity", "limit": n}      -> {"records": [...]}

Les échanges sont chiffrés en TLS 1.3, avec un certificat dérivé de la clé du
nœud. L'adresse d'un nœud contient cette clé, et le client vérifie que le nœud
joint la détient avant de lui envoyer quoi que ce soit : un imposteur à cette
adresse est détecté et écarté.

Côté utilisateur, la synchronisation est au mieux : on joint tous les pairs
qu'on connaît (ceux de sa configuration, puis les adresses annoncées par son
voisinage de confiance, les plus proches d'abord), on passe ceux qui ne
répondent pas, on publie ses propres enregistrements et on récupère ceux de
son voisinage. Son annonce liste son propre nœud et les pairs qui acceptent
ses notes, pour que ses contacts sachent où les trouver même quand son nœud
est éteint.
"""

import http.client
import json
import os
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tospesa.protocol import (
    MAX_ADDRESSES,
    Identity,
    InvalidRecord,
    Record,
    entity_id,
    format_address,
    parse_address,
    public_key_of,
    record_from_json,
)
from tospesa.store import Store, Want

TIMEOUT = 5.0
MAX_BODY = 16 * 1024 * 1024


class Unreachable(Exception):
    pass


class Impostor(Unreachable):
    """Le nœud joint ne détient pas la clé de son adresse."""


# --- serveur ---------------------------------------------------------------


class _TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, context: ssl.SSLContext):
        self._context = context
        super().__init__(address, handler)

    def finish_request(self, request, client_address):
        # La poignée de main TLS se fait dans le fil de la requête, pour qu'un
        # client lent ne bloque pas l'acceptation des autres.
        request.settimeout(TIMEOUT)
        try:
            secure = self._context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            super().finish_request(secure, client_address)
        finally:
            secure.close()


def make_server(
    store: Store,
    identity: Identity,
    certificate: Path,
    host: str,
    port: int,
    accepted_authors: Callable[[], set[str] | None],
) -> ThreadingHTTPServer:
    """`certificate` est le fichier PEM de `identity.tls_pem()`.
    `accepted_authors` renvoie les auteurs dont on accepte les
    enregistrements publiés, ou None pour tout accepter (relais)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certificate)
    lock = threading.Lock()

    def heads(body):
        return {"heads": store.heads(body["authors"])}

    def push(body):
        reply = {"added": 0, "refused": 0, "rejected": 0}
        accepted = accepted_authors()
        for line in body["records"]:
            try:
                record = record_from_json(line)
                record.verify()
            except InvalidRecord:
                reply["rejected"] += 1
                continue
            if accepted is not None and record.author_id not in accepted:
                reply["refused"] += 1
            else:
                reply["added"] += store.add(record)
        return reply

    def pull(body):
        wants = [tuple(want) for want in body["wants"]]
        return {"records": _dump(store.records_for(wants, body["limit"]))}

    def trace(body):
        notes = store.trace(body["viewer"], body["entity"], body["limit"])
        return {"records": _dump(notes)}

    routes = {"/heads": heads, "/push": push, "/pull": pull, "/trace": trace}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self.send_error(413)
                return
            try:
                body = json.loads(self.rfile.read(length))
                with lock:
                    store.refresh()
                    reply = route(body)
            except (ValueError, KeyError, TypeError) as error:
                self.send_error(400, str(error))
                return
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return _TLSServer((host, port), Handler, context)


def _dump(records: Iterable[Record]) -> list[str]:
    return [record.to_json() for record in records]


# --- client ----------------------------------------------------------------

_CLIENT_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_CLIENT_CONTEXT.minimum_version = ssl.TLSVersion.TLSv1_3
# Pas d'autorité de certification : on vérifie nous-mêmes que le certificat
# présenté porte la clé de l'adresse. TLS 1.3 garantit que le serveur détient
# la clé privée de ce certificat.
_CLIENT_CONTEXT.check_hostname = False
_CLIENT_CONTEXT.verify_mode = ssl.CERT_NONE


class RemoteNode:
    """Un nœud distant, vu comme un pair : `Store.pull` s'en sert tel quel.
    Les enregistrements invalides qu'il renvoie sont écartés et comptés."""

    def __init__(self, address: str):
        self.address = address
        self.key, self.host, self.port = parse_address(address)
        self.rejected = 0

    def _post(self, endpoint: str, body: dict) -> dict:
        connection = http.client.HTTPSConnection(
            self.host, self.port, context=_CLIENT_CONTEXT, timeout=TIMEOUT
        )
        try:
            connection.connect()
            certificate = connection.sock.getpeercert(binary_form=True)
            try:
                presented = public_key_of(certificate) if certificate else None
            except (InvalidRecord, ValueError):
                presented = None
            if presented != self.key:
                raise Impostor(f"{self.address} ne présente pas la clé de son adresse")
            connection.request(
                "POST", endpoint, json.dumps(body), {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            data = response.read(MAX_BODY + 1)
            if response.status != 200 or len(data) > MAX_BODY:
                raise Unreachable(f"{self.address} : réponse {response.status}")
            return json.loads(data)
        except (OSError, http.client.HTTPException, ValueError, InvalidRecord) as error:
            raise Unreachable(f"{self.address} : {error}") from error
        finally:
            connection.close()

    def _records(self, lines: list[str]) -> list[Record]:
        records = []
        for line in lines:
            try:
                record = record_from_json(line)
                record.verify()
                records.append(record)
            except InvalidRecord:
                self.rejected += 1
        return records

    def heads(self, authors: list[str]) -> list[Want]:
        return [tuple(want) for want in self._post("/heads", {"authors": authors})["heads"]]

    def push(self, records: Iterable[Record]) -> dict:
        return self._post("/push", {"records": _dump(records)})

    def records_for(self, wants: Iterable[Want], limit: int | None = None) -> list[Record]:
        reply = self._post("/pull", {"wants": list(wants), "limit": limit})
        return self._records(reply["records"])

    def trace(self, viewer: str, entity: str, limit: int | None = None) -> list[Record]:
        reply = self._post("/trace", {"viewer": viewer, "entity": entity, "limit": limit})
        return self._records(reply["records"])


# --- dossier utilisateur ---------------------------------------------------


class Home:
    """Le dossier d'un utilisateur (ou d'un relais) : clé, configuration,
    copie locale."""

    def __init__(self, path: Path):
        self.path = path
        self.store = Store(path / "records.jsonl")

    @property
    def _key_file(self) -> Path:
        return self.path / "key"

    def has_identity(self) -> bool:
        return self._key_file.exists()

    def create_identity(self) -> Identity:
        if self.has_identity():
            raise SystemExit(f"une clé existe déjà dans {self.path}")
        identity = Identity.generate()
        self._write_private(self._key_file, identity.private_hex().encode())
        return identity

    def identity(self) -> Identity:
        if not self.has_identity():
            raise SystemExit(f"pas de clé dans {self.path} : lancer d'abord `init`")
        return Identity.from_private_hex(self._key_file.read_text())

    def certificate(self) -> Path:
        """Fichier PEM (clé et certificat) pour chiffrer les échanges du nœud."""
        path = self.path / "tls.pem"
        self._write_private(path, self.identity().tls_pem())
        return path

    def _write_private(self, path: Path, data: bytes) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)

    def config(self) -> dict:
        path = self.path / "config.json"
        config = json.loads(path.read_text()) if path.exists() else {}
        return {"peers": [], "address": None, "outbox": [], **config}

    def update_config(self, **changes) -> None:
        config = {**self.config(), **changes}
        (self.path / "config.json").write_text(json.dumps(config, indent=2))

    def add_peer(self, address: str) -> None:
        parse_address(address)
        peers = self.config()["peers"]
        if address not in peers:
            self.update_config(peers=[*peers, address])

    def rate(self, entity: str, value: float | None, weight: float = 1.0) -> None:
        """Note (ou retire sa note, si value est None) l'entité désignée par
        sa chaîne d'identification."""
        me = self.identity()
        self.store.refresh()
        target = entity_id(entity)
        previous = self.store.note(me.id, target)
        timestamp = max(time.time_ns(), previous.timestamp + 1 if previous else 0)
        self.store.add(me.sign_note(target, value, weight, timestamp=timestamp))


# --- synchronisation -------------------------------------------------------


@dataclass
class SyncReport:
    added: int = 0
    rejected: int = 0
    reached: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    impostors: list[str] = field(default_factory=list)


def _refresh_announce(home: Home, me: Identity) -> bool:
    """Met à jour son annonce si ses adresses ont changé. Renvoie True si une
    nouvelle annonce a été signée."""
    config = home.config()
    addresses = [config["address"]] if config["address"] else []
    addresses += [address for address in config["outbox"] if address not in addresses]
    addresses = addresses[:MAX_ADDRESSES]
    current = home.store.announce(me.id)
    if current is None and not addresses:
        return False
    if current is not None and list(current.addresses) == addresses:
        return False
    timestamp = max(time.time_ns(), current.timestamp + 1 if current else 0)
    home.store.add(me.sign_announce(addresses, timestamp=timestamp))
    return True


def _candidates(home: Home, me: Identity) -> list[str]:
    config = home.config()
    addresses = list(config["peers"])
    neighborhood = [me.id, *(e for e, _, _ in home.store.network().neighborhood(me.id))]
    for author in neighborhood:
        announce = home.store.announce(author)
        if announce:
            addresses += announce.addresses
    own = config["address"]
    return [address for address in dict.fromkeys(addresses) if address != own]


def _push_own(remote: RemoteNode, store: Store, me: Identity) -> bool:
    """Publie ses enregistrements que le pair n'a pas encore. Renvoie True si
    le pair les accepte."""
    _, notes_since, announce_since = remote.heads([me.id])[0]
    records: list[Record] = [
        note
        for note in store.notes()
        if note.author_id == me.id and note.timestamp > notes_since
    ]
    announce = store.announce(me.id)
    if announce and announce.timestamp > announce_since:
        records.append(announce)
    if not records:
        return True
    reply = remote.push(records)
    return reply["refused"] == 0 and reply["rejected"] == 0


def _visit_peers(
    home: Home, me: Identity, exchange: Callable[[RemoteNode], int]
) -> SyncReport:
    """Joint chaque pair connu, y compris ceux découverts en chemin, et y
    publie ses enregistrements avant l'échange demandé."""
    home.store.refresh()
    _refresh_announce(home, me)
    report = SyncReport()
    accepting: list[str] = []
    visited: set[str] = set()
    while pending := [a for a in _candidates(home, me) if a not in visited]:
        for address in pending:
            visited.add(address)
            remote = RemoteNode(address)
            try:
                if _push_own(remote, home.store, me):
                    accepting.append(address)
                report.added += exchange(remote)
            except Impostor:
                report.impostors.append(address)
                continue
            except Unreachable:
                report.unreachable.append(address)
                continue
            report.reached.append(address)
            report.rejected += remote.rejected
    home.update_config(outbox=accepting)
    if _refresh_announce(home, me):
        for address in accepting:
            try:
                _push_own(RemoteNode(address), home.store, me)
            except Unreachable:
                pass  # il recevra la nouvelle annonce à la prochaine synchronisation
    return report


def sync(home: Home) -> SyncReport:
    """Publie ses enregistrements et récupère ceux de son voisinage de
    confiance auprès de tous les pairs joignables."""
    me = home.identity()
    return _visit_peers(home, me, lambda remote: home.store.pull(remote, me.id))


def lookup(home: Home, entity: str, limit: int | None = None) -> SyncReport:
    """Recherche ciblée : demande à chaque pair joignable qui, dans son réseau
    de confiance, a noté l'entité (désignée par sa chaîne), et récupère les
    chaînes de notes correspondantes."""
    me = home.identity()

    def exchange(remote: RemoteNode) -> int:
        found = Store()
        for note in remote.trace(me.id, entity_id(entity), limit):
            found.add(note)
        return home.store.pull(found, me.id)

    return _visit_peers(home, me, exchange)


# --- nœud en fonctionnement ------------------------------------------------


class Node:
    """Un nœud qui sert la copie d'un dossier : personnel, ou relais.

    `public_host` / `public_port` : comment les autres joignent ce nœud, s'ils
    diffèrent de l'interface d'écoute (machine derrière un routeur, etc.)."""

    def __init__(self, home: Home, host: str = "127.0.0.1", port: int = 0, *,
                 public_host: str | None = None, public_port: int | None = None,
                 relay: bool = False):
        self.home = home
        self.relay = relay
        identity = home.identity()
        if relay:
            accepted = lambda: None  # noqa: E731
        else:

            def accepted() -> set[str]:
                net = home.store.network()
                return {identity.id, *(e for e, _, _ in net.neighborhood(identity.id))}

        home.store.compact()
        self.server = make_server(
            home.store, identity, home.certificate(), host, port, accepted
        )
        self.address = format_address(
            identity.public, public_host or host, public_port or self.server.server_port
        )
        if not relay:
            home.update_config(address=self.address)
            _refresh_announce(home, identity)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "Node":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def run_forever(self, interval: float, report: Callable[[SyncReport], None]) -> None:
        self.start()
        # Copie distincte de celle que sert le serveur : elles partagent le
        # journal et relisent chacune ce que l'autre y ajoute.
        home = Home(self.home.path)
        while True:
            if not self.relay:
                report(sync(home))
            time.sleep(interval)
