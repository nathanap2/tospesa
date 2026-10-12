"""Interface minimale en ligne de commande.

Chaque utilisateur a un dossier (--home) qui contient sa clé privée et sa
copie locale des notes : les siennes et celles qu'il a importées. Les
chaînes passées en argument sont hachées telles quelles ; pour noter une
personne, on passe sa clé publique.
"""

import argparse
import time
from pathlib import Path

from tospesa.protocol import Identity, InvalidNote, Note, entity_id
from tospesa.store import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tospesa")
    parser.add_argument("--home", type=Path, default=Path.home() / ".tospesa")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="créer sa clé")
    commands.add_parser("id", help="afficher sa clé publique")
    rate = commands.add_parser("rate", help="noter une entité")
    rate.add_argument("entity")
    rate.add_argument("value", type=float)
    rate.add_argument("--weight", type=float, default=1.0)
    commands.add_parser("unrate", help="retirer sa note").add_argument("entity")
    commands.add_parser("score", help="consulter son score").add_argument("entity")
    commands.add_parser("export", help="exporter ses notes").add_argument("file", type=Path)
    commands.add_parser("import", help="importer des notes").add_argument("file", type=Path)
    args = parser.parse_args(argv)

    home: Path = args.home
    key_file, notes_file = home / "key", home / "notes.jsonl"

    if args.command == "init":
        if key_file.exists():
            raise SystemExit(f"une clé existe déjà dans {home}")
        home.mkdir(parents=True, exist_ok=True)
        identity = Identity.generate()
        key_file.write_text(identity.private_hex())
        print(identity.public)
        return

    if not key_file.exists():
        raise SystemExit(f"pas de clé dans {home} : lancer d'abord `init`")
    me = Identity.from_private_hex(key_file.read_text())
    store = Store.load(notes_file)

    if args.command == "id":
        print(me.public)
    elif args.command in ("rate", "unrate"):
        target = entity_id(args.entity)
        value = args.value if args.command == "rate" else None
        weight = args.weight if args.command == "rate" else 1.0
        previous = store.latest(me.public, target)
        timestamp = max(time.time_ns(), previous.timestamp + 1 if previous else 0)
        store.add(me.sign_note(target, value, weight, timestamp=timestamp))
        store.save(notes_file)
    elif args.command == "score":
        print(f"{store.network().score(me.id, entity_id(args.entity)):+.2f}")
    elif args.command == "export":
        store.save(args.file)
    elif args.command == "import":
        added = rejected = 0
        for line in args.file.read_text().splitlines():
            try:
                added += store.add(Note.from_json(line))
            except InvalidNote as error:
                rejected += 1
                print(f"note rejetée : {error}")
        store.save(notes_file)
        print(f"{added} notes nouvelles, {rejected} rejetées")


if __name__ == "__main__":
    main()
