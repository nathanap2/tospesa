"""Interface minimale en ligne de commande.

Chaque utilisateur a un dossier (--home) qui contient sa clé privée, sa
configuration et sa copie locale des enregistrements : les siens et ceux de
son voisinage de confiance. Les chaînes passées en argument sont hachées
telles quelles ; pour noter une personne, on passe sa clé publique.
"""

import argparse
from pathlib import Path

from tospesa.node import Home, Node, SyncReport, lookup, sync
from tospesa.protocol import InvalidRecord, entity_id, record_from_json
from tospesa.store import Store


def _parser() -> argparse.ArgumentParser:
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
    score = commands.add_parser("score", help="consulter son score")
    score.add_argument("entity")
    score.add_argument("--local", action="store_true", help="sans interroger le réseau")
    score.add_argument("--limit", type=int, default=None, help="nombre de notateurs cherchés")
    commands.add_parser("export", help="exporter sa copie").add_argument("file", type=Path)
    commands.add_parser("import", help="importer un fichier").add_argument("file", type=Path)
    peers = commands.add_parser("peers", help="pairs connus (relais, nœuds)")
    peers.add_argument("action", nargs="?", choices=["add", "remove"])
    peers.add_argument("address", nargs="?", help="tsp://<clé>@hôte:port")
    commands.add_parser("sync", help="se synchroniser avec ses pairs")
    for name, label in (("node", "son nœud"), ("relay", "un relais")):
        server = commands.add_parser(name, help=f"faire tourner {label}")
        server.add_argument("--host", default="127.0.0.1", help="interface d'écoute")
        server.add_argument("--port", type=int, default=8470)
        server.add_argument("--public-host", help="hôte par lequel les autres le joignent")
        server.add_argument("--public-port", type=int, help="port par lequel les autres le joignent")
        server.add_argument("--interval", type=float, default=60.0,
                            help="secondes entre deux synchronisations")
    return parser


def _print_report(report: SyncReport) -> None:
    print(
        f"{report.added} enregistrements nouveaux de ton réseau ; "
        f"pairs joints : {len(report.reached)}, injoignables : {len(report.unreachable)}, "
        f"enregistrements invalides écartés : {report.rejected}",
        flush=True,
    )
    for address in report.impostors:
        print(f"attention : {address} ne détient pas la clé de son adresse", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    home = Home(args.home)

    if args.command in ("node", "relay"):
        if args.command == "relay" and not home.has_identity():
            home.create_identity()
        node = Node(home, args.host, args.port, public_host=args.public_host,
                    public_port=args.public_port, relay=args.command == "relay")
        print(node.address, flush=True)
        node.run_forever(args.interval, _print_report)
    elif args.command == "init":
        print(home.create_identity().public)
    elif args.command == "id":
        print(home.identity().public)
    elif args.command == "rate":
        home.rate(args.entity, args.value, args.weight)
    elif args.command == "unrate":
        home.rate(args.entity, None)
    elif args.command == "score":
        me = home.identity()
        value, certainty = home.store.network().opinion(me.id, entity_id(args.entity))
        print(f"{value:+.2f}  certitude {certainty:.2f}  (copie locale)", flush=True)
        if not args.local and lookup(home, args.entity, args.limit).reached:
            value, certainty = home.store.network().opinion(me.id, entity_id(args.entity))
            print(f"{value:+.2f}  certitude {certainty:.2f}  (réseau)")
    elif args.command == "export":
        home.store.save(args.file)
    elif args.command == "import":
        me = home.identity()
        peer, rejected = Store(), 0
        for line in args.file.read_text().splitlines():
            try:
                peer.add(record_from_json(line))
            except InvalidRecord as error:
                rejected += 1
                print(f"enregistrement rejeté : {error}")
        added = home.store.pull(peer, me.id)
        print(f"{added} enregistrements nouveaux de ton réseau, {rejected} rejetés")
    elif args.command == "peers":
        config = home.config()
        if args.action == "add":
            home.add_peer(args.address)
        elif args.action == "remove":
            home.update_config(peers=[a for a in config["peers"] if a != args.address])
        else:
            print("\n".join(config["peers"]))
    elif args.command == "sync":
        home.identity()
        _print_report(sync(home))


if __name__ == "__main__":
    main()
