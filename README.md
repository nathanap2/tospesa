# tospesa

Savoir en quoi on peut avoir confiance — des personnes, des livres, des sites,
des lieux, des entreprises… — à partir de l'avis des gens en qui **toi** tu as
confiance, et de ceux en qui eux ont confiance. Chacun voit un score qui lui
est propre. Il n'y a pas de serveur central : ce sont les machines des
participants qui forment le réseau.

## Installer

Il faut [uv](https://docs.astral.sh/uv/getting-started/installation/) :

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh        # Linux, macOS
# Windows (PowerShell) : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Puis :

```sh
uv tool install git+https://github.com/nathanap2/tospesa
```

## Démarrer

```sh
tospesa init --name Alice     # crée ta clé ; le nom est proposé à ceux que tu invites
tospesa node                  # fait tourner ton nœud ; laisse ce terminal ouvert
```

Ton nœud essaie d'ouvrir automatiquement un port sur ta box (comme les clients
BitTorrent) et détecte ton adresse IPv6 si tu en as une. Il synchronise tes
notes avec le réseau toutes les minutes. S'il n'est pas joignable de
l'extérieur, ce n'est pas grave : tes notes sont publiées chez tes amis
joignables.

## Inviter un ami, rejoindre un ami

Dans un autre terminal :

```sh
tospesa invite                # affiche un code « tospesa:… » à envoyer à un ami
tospesa join tospesa:…        # accepte le code reçu d'un ami
```

Accepter une invitation ajoute l'ami à ton carnet de contacts (tu peux ensuite
le désigner par `@son-nom`), lui fait confiance, et te donne un point d'entrée
dans le réseau. **Échangez vos invitations dans les deux sens** : ton nœud
n'accepte que les notes des gens de ton réseau de confiance.

## Noter, consulter

Une note va de `-1` (« je ne suis pas du tout raccord ») à `1` (« entièrement
raccord »).

```sh
tospesa rate @Bob 1                              # une personne de tes contacts
tospesa rate https://fr.wikipedia.org/wiki/Bourbaki 0.8
tospesa rate "isbn:<ISBN sans tirets>" 1        # un livre
tospesa rate "Boulangerie Dupont, 12 rue X, Lyon" -0.5
tospesa score https://fr.wikipedia.org/wiki/Bourbaki
```

`score` affiche d'abord ce que dit ta copie locale, puis le résultat après
avoir interrogé ton réseau, avec la certitude et la distance de la source la
plus sûre.

Les URL sont normalisées (domaine en minuscules, sans `/` final…). Toute autre
chaîne est prise **telle quelle** : pour que vous tombiez sur la même entité,
mettez-vous d'accord sur une écriture (par exemple `isbn:` suivi de l'ISBN
sans tirets pour un livre).

`--weight` (entre 0 et 1) indique à quel point tu engages ta note :
`tospesa rate @Bob 1 --weight 0.3` pour « plutôt confiance, sans plus ».

## Ce qui circule

Tes notes sont signées par ta clé et publiques pour ton réseau. Ce qui
circule, ce n'est jamais le texte de ce que tu notes, seulement son empreinte
(un hash) : quelqu'un qui connaît déjà le texte peut retrouver que tu l'as
noté, mais on ne peut pas lire la liste de ce que tu as noté. Les échanges
entre nœuds sont chiffrés.

## Autres commandes

`tospesa contacts`, `tospesa sync` (synchroniser tout de suite), `tospesa
peers` (points d'entrée connus), `tospesa relay` (faire tourner un relais qui
accepte les notes de tout le monde, sur une machine toujours allumée),
`tospesa --help`.
