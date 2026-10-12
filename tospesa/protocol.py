"""Protocole : notes signées sur des empreintes d'entités.

Une entité est désignée dans le réseau par l'empreinte SHA-256 de la chaîne
qui l'identifie, jamais par la chaîne elle-même. Un utilisateur est une
entité dont la chaîne est sa clé publique (Ed25519, en hexadécimal).

Une note est autonome : elle porte la clé de son auteur et sa signature, et
se vérifie sans rien demander à personne. Elle peut donc transiter par
n'importe quel pair. Une note de valeur None est un retrait.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

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


class InvalidNote(Exception):
    pass


def entity_id(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()


@dataclass(frozen=True)
class Note:
    author: str  # clé publique, hexadécimal
    target: str  # empreinte de l'entité notée
    value: float | None
    weight: float
    timestamp: int
    signature: str

    @property
    def author_id(self) -> str:
        return entity_id(self.author)

    def payload(self) -> bytes:
        fields = asdict(self)
        del fields["signature"]
        return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()

    def verify(self) -> None:
        if len(self.target) != 64:
            raise InvalidNote(f"empreinte mal formée : {self.target!r}")
        if self.target == self.author_id:
            raise InvalidNote("un auteur ne se note pas lui-même")
        if self.value is not None and not -1 <= self.value <= 1:
            raise InvalidNote(f"valeur hors de [-1, 1] : {self.value}")
        if not 0 < self.weight <= 1:
            raise InvalidNote(f"pondération hors de ]0, 1] : {self.weight}")
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.author))
            key.verify(bytes.fromhex(self.signature), self.payload())
        except (ValueError, InvalidSignature) as error:
            raise InvalidNote("signature invalide") from error

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "Note":
        try:
            return cls(**json.loads(line))
        except (TypeError, ValueError) as error:
            raise InvalidNote(f"note illisible : {line!r}") from error


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

    def sign_note(
        self, target: str, value: float | None, weight: float = 1.0, *, timestamp: int
    ) -> Note:
        value = None if value is None else float(value)
        weight = float(weight)
        unsigned = Note(self.public, target, value, weight, timestamp, signature="")
        signature = self._key.sign(unsigned.payload()).hex()
        note = Note(self.public, target, value, weight, timestamp, signature)
        note.verify()
        return note
