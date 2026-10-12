"""Protocole : enregistrements signés, autonomes et vérifiables.

Une entité est désignée dans le réseau par l'empreinte SHA-256 de la chaîne
qui l'identifie, jamais par la chaîne elle-même. Un utilisateur est une
entité dont la chaîne est sa clé publique (Ed25519, en hexadécimal).

Deux types d'enregistrements, chacun signé par son auteur et vérifiable sans
rien demander à personne, donc transmissibles par n'importe quel pair :
- la note, sur une entité ; une note de valeur None est un retrait ;
- l'annonce, qui liste les adresses où l'on peut trouver les notes de son
  auteur (son propre nœud, les relais qui les acceptent).

L'adresse d'un nœud contient la clé publique de son propriétaire :
`tsp://<clé>@hôte:port`. Les échanges entre nœuds sont chiffrés (TLS 1.3) avec
un certificat dérivé de cette clé ; le client vérifie que le nœud joint détient
bien la clé de l'adresse.
"""

import datetime
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import ClassVar

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.x509.oid import NameOID

MAX_ADDRESSES = 8
_ADDRESS = re.compile(r"tsp://([0-9a-f]{64})@([A-Za-z0-9.\-]{1,100}):([0-9]{1,5})")


class InvalidRecord(Exception):
    pass


def entity_id(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()


def format_address(key: str, host: str, port: int) -> str:
    return f"tsp://{key}@{host}:{port}"


def parse_address(address: str) -> tuple[str, str, int]:
    """(clé publique, hôte, port) d'une adresse de nœud."""
    match = _ADDRESS.fullmatch(address)
    if match is None or not 0 < int(match[3]) < 65536:
        raise InvalidRecord(f"adresse mal formée : {address!r}")
    return match[1], match[2], int(match[3])


def public_key_of(certificate_der: bytes) -> str:
    """Clé publique (hexadécimal) d'un certificat TLS présenté par un nœud."""
    key = x509.load_der_x509_certificate(certificate_der).public_key()
    if not isinstance(key, Ed25519PublicKey):
        raise InvalidRecord("le certificat n'est pas une clé Ed25519")
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


@dataclass(frozen=True)
class Signed:
    kind: ClassVar[str]
    author: str  # clé publique, hexadécimal
    timestamp: int

    @property
    def author_id(self) -> str:
        return entity_id(self.author)

    def payload(self) -> bytes:
        fields = asdict(self)
        del fields["signature"]
        fields["kind"] = self.kind
        return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()

    def check(self) -> None:
        """Contraintes propres au type d'enregistrement."""

    def verify(self) -> None:
        self.check()
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.author))
            key.verify(bytes.fromhex(self.signature), self.payload())
        except (ValueError, InvalidSignature) as error:
            raise InvalidRecord("signature invalide") from error

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, **asdict(self)}, sort_keys=True)


@dataclass(frozen=True)
class Note(Signed):
    kind: ClassVar[str] = "note"
    target: str  # empreinte de l'entité notée
    value: float | None
    weight: float
    signature: str

    def check(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.target):
            raise InvalidRecord(f"empreinte mal formée : {self.target!r}")
        if self.target == self.author_id:
            raise InvalidRecord("un auteur ne se note pas lui-même")
        if self.value is not None and not -1 <= self.value <= 1:
            raise InvalidRecord(f"valeur hors de [-1, 1] : {self.value}")
        if not 0 < self.weight <= 1:
            raise InvalidRecord(f"pondération hors de ]0, 1] : {self.weight}")


@dataclass(frozen=True)
class Announce(Signed):
    kind: ClassVar[str] = "announce"
    addresses: tuple[str, ...]
    signature: str

    def check(self) -> None:
        if len(self.addresses) > MAX_ADDRESSES:
            raise InvalidRecord(f"plus de {MAX_ADDRESSES} adresses")
        for address in self.addresses:
            parse_address(address)


Record = Note | Announce
_KINDS = {cls.kind: cls for cls in (Note, Announce)}


def record_from_json(line: str) -> Record:
    try:
        fields = json.loads(line)
        cls = _KINDS[fields.pop("kind")]
        if cls is Announce:
            fields["addresses"] = tuple(fields["addresses"])
        return cls(**fields)
    except (TypeError, ValueError, KeyError) as error:
        raise InvalidRecord(f"enregistrement illisible : {line[:200]!r}") from error


class Identity:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._key = private_key
        self.public = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        ).hex()
        self.id = entity_id(self.public)

    @classmethod
    def generate(cls) -> "Identity":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_hex(cls, private: str) -> "Identity":
        return cls(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private)))

    def private_hex(self) -> str:
        return self._key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        ).hex()

    def tls_pem(self) -> bytes:
        """Clé privée et certificat autosigné, au format PEM, pour chiffrer les
        échanges du nœud de cette identité."""
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.public)])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self._key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(self._key, None)
        )
        return self._key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ) + certificate.public_bytes(Encoding.PEM)

    def _signed(self, cls: type[Signed], **fields) -> Signed:
        unsigned = cls(author=self.public, signature="", **fields)
        signature = self._key.sign(unsigned.payload()).hex()
        record = cls(author=self.public, signature=signature, **fields)
        record.verify()
        return record

    def sign_note(
        self, target: str, value: float | None, weight: float = 1.0, *, timestamp: int
    ) -> Note:
        value = None if value is None else float(value)
        return self._signed(
            Note, target=target, value=value, weight=float(weight), timestamp=timestamp
        )

    def sign_announce(self, addresses: list[str], *, timestamp: int) -> Announce:
        return self._signed(Announce, addresses=tuple(addresses), timestamp=timestamp)
