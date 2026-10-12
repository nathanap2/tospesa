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
  POST /pull  {"wants": [want, ...]}                -> {"records": [...]}
  POST /trace {"viewer", "entity"}                  -> {"records": [...]}

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

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.parse
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
from tospesa import upnp
from tospesa.store import Store, Want

TIMEOUT = 5.0
SYNC_INTERVAL = 60.0  # secondes entre deux synchronisations du nœud
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
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            # « :: » écoute à la fois en IPv6 et en IPv4.
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

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
        except (ConnectionError, TimeoutError, ssl.SSLError):
            pass  # le client a coupé ou s'est tu : rien à lui répondre
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
        return {"records": _dump(store.records_for(wants))}

    def trace(body):
        notes = store.trace(body["viewer"], body["entity"])
        return {"records": _dump(notes)}

    routes = {"/heads": heads, "/push": push, "/pull": pull, "/trace": trace}

    def probe(body, client_ip):
        # On ne rappelle que l'adresse IP d'où vient la demande : le serveur ne
        # peut pas servir à sonder des machines tierces.
        _, host, _ = parse_address(body["address"])
        if client_ip.startswith("::ffff:"):
            client_ip = client_ip[len("::ffff:"):]
        if ipaddress.ip_address(host) != ipaddress.ip_address(client_ip):
            return {"reachable": False}
        try:
            RemoteNode(body["address"]).heads([])
        except Unreachable:
            return {"reachable": False}
        return {"reachable": True}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/probe":
                route = lambda body: probe(body, self.client_address[0])  # noqa: E731
            else:
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
                if self.path == "/probe":
                    reply = route(body)
                else:
                    with lock:
                        store.refresh()
                        reply = route(body)
            except (ValueError, KeyError, TypeError, InvalidRecord) as error:
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

    def probe(self, address: str) -> bool:
        """Demande au nœud distant d'essayer de nous joindre à cette adresse."""
        return bool(self._post("/probe", {"address": address})["reachable"])

    def heads(self, authors: list[str]) -> list[Want]:
        return [tuple(want) for want in self._post("/heads", {"authors": authors})["heads"]]

    def push(self, records: Iterable[Record]) -> dict:
        return self._post("/push", {"records": _dump(records)})

    def records_for(self, wants: Iterable[Want]) -> list[Record]:
        reply = self._post("/pull", {"wants": list(wants)})
        return self._records(reply["records"])

    def trace(self, viewer: str, entity: str) -> list[Record]:
        reply = self._post("/trace", {"viewer": viewer, "entity": entity})
        return self._records(reply["records"])


# --- dossier utilisateur ---------------------------------------------------

INVITATION_PREFIX = "tospesa:"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Forme canonique d'une URL : schéma et domaine en minuscules, sans port
    par défaut, sans fragment, sans « / » final. Le chemin et la requête sont
    gardés tels quels (ils peuvent être sensibles à la casse)."""
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, host, path, parts.query, ""))



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
        # addresses : adresses de son nœud, annoncées (données explicitement ou
        # vérifiées par un pair) ; candidates : adresses détectées, à vérifier.
        return {"peers": [], "addresses": [], "candidates": [], "outbox": [], **config}

    def update_config(self, **changes) -> None:
        config = {**self.config(), **changes}
        (self.path / "config.json").write_text(json.dumps(config, indent=2))

    def add_peer(self, address: str) -> None:
        parse_address(address)
        peers = self.config()["peers"]
        if address not in peers:
            self.update_config(peers=[*peers, address])

    # --- contacts et désignation des entités ---------------------------

    def contacts(self) -> dict[str, str]:
        """Carnet local : nom -> clé publique. Il n'est jamais publié."""
        path = self.path / "contacts.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def add_contact(self, name: str, key: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise SystemExit(f"clé mal formée : {key!r}")
        contacts = self.contacts()
        if contacts.get(name, key) != key:
            raise SystemExit(f"le contact {name!r} existe déjà avec une autre clé")
        contacts[name] = key
        (self.path / "contacts.json").write_text(json.dumps(contacts, indent=2))

    def target(self, entity: str) -> str:
        """Empreinte de l'entité désignée par l'utilisateur :
        - « @nom » : un contact du carnet (on note sa clé publique) ;
        - une URL http(s) : normalisée, pour que ses différentes écritures
          désignent la même entité ;
        - toute autre chaîne : hachée telle quelle."""
        if entity.startswith("@"):
            key = self.contacts().get(entity[1:])
            if key is None:
                raise SystemExit(f"contact inconnu : {entity}")
            return entity_id(key)
        if re.match(r"https?://", entity, re.IGNORECASE):
            return entity_id(normalize_url(entity))
        return entity_id(entity)

    # --- invitations ----------------------------------------------------

    def invitation(self) -> str:
        """Code à envoyer à un ami : sa clé et les adresses où le joindre (son
        nœud, et les pairs qu'il utilise)."""
        me = self.identity()
        config = self.config()
        addresses = [*config["addresses"], *config["candidates"], *config["peers"]]
        payload = {"key": me.public, "addresses": list(dict.fromkeys(addresses))}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return INVITATION_PREFIX + encoded.rstrip("=")

    def join(self, invitation: str, name: str) -> None:
        """Accepte une invitation : ajoute l'ami au carnet sous ce nom, lui
        fait confiance, et prend ses adresses comme points d'entrée."""
        if not invitation.startswith(INVITATION_PREFIX):
            raise SystemExit("ce n'est pas une invitation tospesa")
        encoded = invitation[len(INVITATION_PREFIX):]
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            key, addresses = payload["key"], payload["addresses"]
            for address in addresses:
                parse_address(address)
        except (ValueError, KeyError, TypeError, InvalidRecord) as error:
            raise SystemExit(f"invitation illisible : {error}") from error
        if key == self.identity().public:
            raise SystemExit("c'est ta propre invitation")
        self.add_contact(name, key)
        own = {*self.config()["addresses"], *self.config()["candidates"]}
        for address in addresses:
            if address not in own:
                self.add_peer(address)
        self.rate("@" + name, 1)

    def rate(self, entity: str, value: float | None, weight: float = 1.0) -> None:
        """Note (ou retire sa note, si value est None) l'entité désignée par
        l'utilisateur (voir `target`)."""
        me = self.identity()
        self.store.refresh()
        target = self.target(entity)
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
    addresses = list(config["addresses"])
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
    own = {*config["addresses"], *config["candidates"]}
    return [address for address in dict.fromkeys(addresses) if address not in own]


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
            _verify_candidates(home, remote)
    home.update_config(outbox=accepting)
    if _refresh_announce(home, me):
        for address in accepting:
            try:
                _push_own(RemoteNode(address), home.store, me)
            except Unreachable:
                pass  # il recevra la nouvelle annonce à la prochaine synchronisation
    return report


def _verify_candidates(home: Home, remote: RemoteNode) -> None:
    """Fait vérifier par le pair les adresses détectées de son nœud : seules
    celles où il parvient à le joindre seront annoncées."""
    config = home.config()
    verified = []
    for candidate in config["candidates"]:
        try:
            if remote.probe(candidate):
                verified.append(candidate)
        except Unreachable:
            return
    if verified:
        home.update_config(
            addresses=[*config["addresses"], *verified],
            candidates=[c for c in config["candidates"] if c not in verified],
        )


def sync(home: Home) -> SyncReport:
    """Publie ses enregistrements et récupère ceux de son voisinage de
    confiance auprès de tous les pairs joignables."""
    me = home.identity()
    return _visit_peers(home, me, lambda remote: home.store.pull(remote, me.id))


def lookup(home: Home, target: str) -> SyncReport:
    """Recherche ciblée : demande à chaque pair joignable qui, dans son réseau
    de confiance, a noté l'entité (son empreinte), et récupère les chaînes de
    notes correspondantes."""
    me = home.identity()

    def exchange(remote: RemoteNode) -> int:
        found = Store()
        for note in remote.trace(me.id, target):
            found.add(note)
        return home.store.pull(found, me.id)

    return _visit_peers(home, me, exchange)


# --- nœud en fonctionnement ------------------------------------------------


_WILDCARDS = ("", "::", "0.0.0.0")


def global_ipv6() -> str | None:
    """Adresse IPv6 publique de cette machine, s'il y en a une. (Se « connecter »
    en UDP n'envoie aucun paquet : ça sert juste à choisir l'adresse source.)"""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
            probe.connect(("2001:4860:4860::8888", 53))
            address = probe.getsockname()[0]
    except OSError:
        return None
    return address if ipaddress.IPv6Address(address).is_global else None


class Node:
    """Un nœud qui sert la copie d'un dossier : personnel, ou relais.

    Ses adresses publiques :
    - données explicitement (`public_host`, ou une interface d'écoute précise) :
      annoncées telles quelles ;
    - sinon, s'il écoute sur toutes les interfaces : détectées (ouverture de
      port UPnP sur la box, adresse IPv6 publique), puis vérifiées par un pair
      lors des synchronisations avant d'être annoncées. Un nœud que personne
      ne peut joindre participe quand même : ses notes sont publiées chez ses
      pairs."""

    def __init__(self, home: Home, host: str = "127.0.0.1", port: int = 0, *,
                 public_host: str | None = None,
                 relay: bool = False,
                 find_gateway: Callable[[], upnp.Gateway] = upnp.discover,
                 find_ipv6: Callable[[], str | None] = global_ipv6):
        self.home = home
        self.relay = relay
        self.identity = identity = home.identity()
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
        self.port = self.server.server_port
        self._find_gateway, self._find_ipv6 = find_gateway, find_ipv6
        self._gateway: upnp.Gateway | None = None
        self.upnp_error: str | None = None
        if public_host or host not in _WILDCARDS:
            self._given = [format_address(identity.public, public_host or host, self.port)]
        else:
            self._given = []
        self.refresh_reachability()
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def addresses(self) -> list[str]:
        """Toutes les adresses connues de ce nœud, vérifiées ou non."""
        return list(dict.fromkeys([*self._given, *self._detected]))

    @property
    def address(self) -> str:
        if not self.addresses:
            raise RuntimeError("ce nœud ne connaît aucune de ses adresses publiques")
        return self.addresses[0]

    def refresh_reachability(self) -> None:
        """(Re)détecte ses adresses publiques et renouvelle l'ouverture de port.
        Appelée au démarrage puis à chaque tour du nœud."""
        detected = []
        if not self._given:
            try:
                gateway = self._gateway or self._find_gateway()
                gateway.add_mapping(self.port)
                detected.append(format_address(self.identity.public,
                                               gateway.external_ip(), self.port))
                self._gateway, self.upnp_error = gateway, None
            except upnp.UPnPError as error:
                self._gateway, self.upnp_error = None, str(error)
            ipv6 = self._find_ipv6()
            if ipv6:
                detected.append(format_address(self.identity.public, ipv6, self.port))
        self._detected = detected
        if self.relay:
            return
        config = self.home.config()
        verified = [a for a in config["addresses"] if a in detected]
        self.home.update_config(
            addresses=[*self._given, *verified],
            candidates=[a for a in detected if a not in verified],
        )
        _refresh_announce(self.home, self.identity)

    def start(self) -> "Node":
        self._thread.start()
        return self

    def stop(self) -> bool:
        """Arrête le nœud et referme le port ouvert sur la box. Renvoie True si
        un port a été refermé."""
        self.server.shutdown()
        self.server.server_close()
        if self._gateway is None:
            return False
        try:
            self._gateway.delete_mapping(self.port)
        except upnp.UPnPError:
            return False  # la redirection expirera d'elle-même (bail d'une heure)
        return True

    def run_forever(self, report: Callable[[SyncReport], None]) -> None:
        self.start()
        # Copie distincte de celle que sert le serveur : elles partagent le
        # journal et relisent chacune ce que l'autre y ajoute.
        home = Home(self.home.path)
        while True:
            self.refresh_reachability()
            if not self.relay:
                report(sync(home))
            time.sleep(SYNC_INTERVAL)
