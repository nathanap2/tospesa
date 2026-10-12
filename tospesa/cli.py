"""Interface en ligne de commande.

Chaque utilisateur a un dossier (--home, par défaut ~/.tospesa) qui contient
sa clé privée, sa configuration, son carnet de contacts et sa copie locale des
enregistrements : les siens et ceux de son voisinage de confiance.

Une entité se désigne par « @nom » (un contact), par une URL (normalisée), ou
par n'importe quelle autre chaîne, hachée telle quelle.
"""

import argparse
import signal
from pathlib import Path

from tospesa.network import Opinion
from tospesa.node import Home, Node, SyncReport, lookup, sync
from tospesa.protocol import InvalidRecord, record_from_json
from tospesa.store import Store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tospesa")
    parser.add_argument("--home", type=Path, default=Path.home() / ".tospesa",
                        help="dossier de l'utilisateur (défaut : ~/.tospesa)")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="créer sa clé")
    init.add_argument("--name", help="nom proposé à ceux que tu invites")
    commands.add_parser("id", help="afficher sa clé publique")
    commands.add_parser("invite", help="afficher un code d'invitation à envoyer à un ami")
    join = commands.add_parser("join", help="accepter l'invitation d'un ami")
    join.add_argument("invitation")
    join.add_argument("--as", dest="name", help="nom sous lequel l'enregistrer")
    commands.add_parser("contacts", help="afficher son carnet de contacts")
    rate = commands.add_parser("rate", help="noter une entité (de -1 à 1)")
    rate.add_argument("entity", help="@nom, URL, ou toute autre chaîne")
    rate.add_argument("value", type=float)
    rate.add_argument("--weight", type=float, default=1.0,
                      help="à quel point tu engages ta note (de 0 exclu à 1)")
    commands.add_parser("unrate", help="retirer sa note").add_argument("entity")
    score = commands.add_parser("score", help="consulter son score")
    score.add_argument("entity", help="@nom, URL, ou toute autre chaîne")
    score.add_argument("--local", action="store_true", help="sans interroger le réseau")
    score.add_argument("--limit", type=int, default=None, help="nombre de notateurs cherchés")
    commands.add_parser("export", help="exporter sa copie").add_argument("file", type=Path)
    commands.add_parser("import", help="importer un fichier").add_argument("file", type=Path)
    peers = commands.add_parser("peers", help="pairs connus (nœuds, relais)")
    peers.add_argument("action", nargs="?", choices=["add", "remove"])
    peers.add_argument("address", nargs="?", help="tsp://<clé>@hôte:port")
    commands.add_parser("sync", help="se synchroniser avec ses pairs")
    for name, label in (("node", "son nœud"), ("relay", "un relais")):
        server = commands.add_parser(name, help=f"faire tourner {label}")
        server.add_argument("--host", default="::",
                            help="interface d'écoute (défaut : toutes, IPv4 et IPv6)")
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


def _print_opinion(opinion: Opinion, origin: str) -> None:
    if opinion.certainty == 0:
        detail = "aucune information"
    elif opinion.depth == 0:
        detail = "ta propre note"
    else:
        detail = f"certitude {opinion.certainty:.2f}, source la plus sûre à {opinion.depth} pas"
    print(f"{opinion.value:+.2f}  ({detail} ; {origin})", flush=True)


def _print_node_start(node: Node) -> None:
    print(f"{'relais' if node.relay else 'nœud'} en écoute sur le port {node.port}")
    for address in node.addresses:
        print(f"  adresse : {address}")
    if node.upnp_error is not None:
        print(f"ouverture automatique de port sur la box impossible : {node.upnp_error}")
    if not node.addresses:
        print("aucune adresse publique : ton nœud ne sera sans doute pas joignable de "
              "l'extérieur, mais tes notes seront publiées chez tes pairs joignables. "
              f"Pour être joignable, redirige le port {node.port} (TCP) sur ta box et "
              "relance avec --public-host <ton adresse publique>.")
    print(flush=True)


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    home = Home(args.home)

    if args.command in ("node", "relay"):
        if args.command == "relay" and not home.has_identity():
            home.create_identity()
        node = Node(home, args.host, args.port, public_host=args.public_host,
                    public_port=args.public_port, relay=args.command == "relay")
        _print_node_start(node)
        # Fermer le terminal (SIGHUP) ou l'arrêt par le système (SIGTERM) doit
        # arrêter le nœud aussi proprement qu'un Ctrl+C, port de la box compris.
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), _interrupt)
        try:
            node.run_forever(args.interval, _print_report)
        except KeyboardInterrupt:
            pass
        finally:
            closed = " et port refermé sur la box" if node.stop() else ""
            print(f"{'relais' if node.relay else 'nœud'} arrêté{closed}.", flush=True)
    elif args.command == "init":
        identity = home.create_identity()
        if args.name:
            home.update_config(name=args.name)
        print(identity.public)
    elif args.command == "id":
        print(home.identity().public)
    elif args.command == "invite":
        print(home.invitation())
    elif args.command == "join":
        name = home.join(args.invitation, args.name)
        print(f"{name} est dans tes contacts (@{name}) et tu lui fais confiance.")
        print(f"pour que {name} reçoive aussi tes notes, envoie-lui ton invitation "
              "(tospesa invite).")
    elif args.command == "contacts":
        for name, key in sorted(home.contacts().items()):
            print(f"@{name}  {key}")
    elif args.command == "rate":
        home.rate(args.entity, args.value, args.weight)
    elif args.command == "unrate":
        home.rate(args.entity, None)
    elif args.command == "score":
        me = home.identity()
        target = home.target(args.entity)
        _print_opinion(home.store.network().opinion(me.id, target), "copie locale")
        if not args.local and lookup(home, target, args.limit).reached:
            _print_opinion(home.store.network().opinion(me.id, target), "réseau")
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
