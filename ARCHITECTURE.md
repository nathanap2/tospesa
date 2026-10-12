# tospesa — pistes d'architecture réseau

Rien ici n'est tranché. Ce sont les pistes qu'on garde ouvertes pour la couche
réseau. Les features utilisateur sont dans `FEATURES.md`.

## Point de départ

On commence par du centralisé. Les notes sont conçues comme des déclarations
signées et portables, pour garder la décentralisation possible plus tard.

## Ce qui existe déjà (prototype)

- **Notes signées** (`tospesa/protocol.py`) : Ed25519, sur l'empreinte SHA-256
  de l'entité. Une note se vérifie seule, donc elle peut transiter par
  n'importe quel pair.
- **Copie locale** (`tospesa/store.py`) : la fusion ne dépend ni de l'ordre
  d'arrivée ni des doublons. La plus récente note de chaque auteur sur chaque
  entité l'emporte. Deux copies qui ont reçu les mêmes notes, par n'importe
  quels chemins, sont identiques. C'est la propriété de base de toute
  synchronisation entre pairs.
- **Synchronisation** (`Store.pull`) : par dates, limitée au voisinage de
  confiance, les auteurs les plus proches d'abord (voir plus bas).
- **Échange manuel** (`tospesa/cli.py`, `export` / `import`) : une
  réplication par fichier, façon Scuttlebutt hors ligne. Un fichier importé
  est traité comme un pair : on n'en tire que son voisinage de confiance.

## Synchronisation entre pairs

Elle fonctionne « au mieux » : une note manquante rend un score un peu moins
précis, ce n'est pas une catastrophe. On ne cherche donc pas de garantie de
complétude coûteuse (numéros de séquence, historique complet).

- *Par dates.* « J'ai les notes de Thomas jusqu'à telle date, donne-moi les
  plus récentes. » On n'échange qu'une date par auteur, puis seulement les
  notes nouvelles. Une note ancienne arrivée en retard chez un pair peut être
  ratée : c'est acceptable.
- *Périmètre.* On ne réplique que son voisinage de confiance : soi-même,
  puis les entités notées positivement, de proche en proche. Les notes
  d'inconnus ne sont jamais copiées, ce qui contient aussi l'injection de
  données.
- *Priorité.* Les auteurs les plus proches en confiance sont envoyés en
  premier, puisque ce sont eux qui pèsent le plus. Une connexion courte
  récupère d'abord ce qui compte.

## Deux couches distinctes

- **Transport** : trouver et récupérer des notes (« quelle note tel
  utilisateur a-t-il posée sur telle entité ? »). Grâce aux signatures, cette
  couche n'a pas besoin de faire confiance à ceux qui stockent ou relaient les
  notes : une note se vérifie. Le seul pouvoir d'un pair malveillant est
  l'**omission**, c'est-à-dire retenir des notes. On limite ce risque en
  recevant les notes par plusieurs sources.
- **Confiance** : pondérer les notes récupérées. Elle guide aussi le transport
  sur un point : l'ordre de récupération. Les notes des auteurs les plus
  connectés à moi en confiance comptent le plus, donc on les récupère en
  premier.

## Estimateur incomplet

Le calcul fonctionne sur n'importe quel sous-ensemble des notes, et la
certitude indique à quel point l'estimation est appuyée. On peut donc
afficher un score tout de suite et l'affiner à mesure que les notes arrivent,
sans attendre le calcul complet.

## Où se fait le calcul

Trois pistes ouvertes, qu'on peut combiner :
- *Notes brutes* : le lecteur récupère les notes signées et calcule lui-même.
  C'est vérifiable, chacun choisit son algorithme, et on peut supprimer tous
  les échos. En contrepartie, les données sont plus volumineuses et les notes
  doivent être lisibles.
- *Avis agrégés* : chaque nœud répond avec son avis calculé (« sans me
  compter »). C'est léger, et ça cache les notes individuelles. En
  contrepartie, on ne peut pas le vérifier et l'écho y est déjà mélangé. Les
  mises à jour se propagent de proche en proche, comme dans un protocole de
  routage *distance-vector*. La règle anti-écho y correspond au *split
  horizon*.
- *Mixte* : notes brutes jusqu'à une certaine distance, avis agrégés au-delà.

Quelle que soit la piste, la règle anti-écho (rien ne revient vers le
lecteur) fait partie du calcul.

## Réplication locale

Chaque client garde une copie des notes de son voisinage de confiance, et ses
contacts lui servent de copies de secours. On consulte en local.

Première simulation (`experiments/neighborhood.py`, 2000 utilisateurs) : pour
une entité populaire, 0 à 2 pas suffisent. Pour une entité moyenne ou de
niche, il faut presque tout le réseau. On complétera donc la réplication
locale par une **recherche ciblée** sur les entités rares : trouver qui les a
notées, puis la chaîne de notes signées qui relie ces auteurs au lecteur.

La requête « qui, dans mon réseau de confiance, a noté cette entité ? »
(utilisée par l'onboarding) suit le même schéma. Elle est locale pour le
voisinage répliqué. Au-delà, elle passe par l'index « entité → auteurs », puis
on vérifie la distance de ces auteurs dans le graphe de confiance.

## Facilitateurs de réseau (indexeurs)

Des nœuds qui agrègent les notes publiques et offrent des réponses rapides,
sur le modèle des indexeurs de Bluesky. Ils donnent un semblant de centralité
sans s'exposer à tous les aléas du décentralisé. N'importe qui peut en monter
un. Comme les notes sont signées, on peut vérifier leurs réponses en les
recalculant sur un échantillon. Ils peuvent aussi servir de point d'entrée
aux nouveaux arrivants, dont la première synchronisation serait lente sinon.

## Existant à étudier

- **Secure Scuttlebutt (SSB)**, à étudier en priorité. C'est un réseau
  social pair-à-pair, actif depuis 2014.
  - *Son fonctionnement.* Chaque utilisateur tient un journal de messages
    signés, en ajout seul. Les journaux sont répliqués le long du graphe
    social : on garde une copie des journaux de ceux qu'on suit, et par
    défaut de ceux qu'ils suivent (2 pas). Il fonctionne hors ligne, et la
    synchronisation se fait quand deux pairs se croisent.
  - *Ce qui correspond à nos idées.* La réplication locale le long du graphe
    de confiance. Les contacts qui servent de copies de secours. Les données
    signées, qu'un pair quelconque peut transmettre sans qu'on ait à lui
    faire confiance. Nos notes pourraient être des messages d'un journal de
    ce type.
  - *Ses retours d'expérience, à lire avant de concevoir notre réplication :*
    - l'espace disque grossit, puisqu'on réplique les journaux entiers ;
    - la première synchronisation d'un nouvel arrivant est lente. SSB l'a
      résolue avec des serveurs relais toujours en ligne (« pubs », puis
      « rooms »), proches de nos facilitateurs de réseau ;
    - des travaux sur la réplication partielle visent à ne récupérer qu'une
      partie d'un journal.
  - *Ce qui nous en distingue.* SSB réplique selon une profondeur fixe et
    sert à échanger des messages. Nous, la confiance pondère les données,
    la profondeur utile dépend de l'entité consultée (voir la simulation),
    et il faut une recherche ciblée pour les entités rares, ce que SSB ne
    fait pas.
  - *À vérifier.* L'état actuel du projet et de sa communauté. Et la
    question de fond : faut-il bâtir sur son protocole, ou seulement
    s'inspirer de ses choix ?
- **Kademlia** (DHT, utilisée par BitTorrent et IPFS). Elle sert à trouver
  quel nœud stocke une clé donnée, selon une distance (XOR) entre
  identifiants, sans rapport avec le graphe de confiance.
  - *Pourquoi elle est pertinente.* Une première intuition l'écartait, au
    motif qu'elle ignore la confiance. C'était à tort : chercher une
    information précise est un problème de transport, pas de confiance. Elle
    pourrait servir de couche de recherche pour deux types de clés :
    - « utilisateur → ses notes », pour trouver où récupérer le journal d'un
      auteur ;
    - « entité → auteurs qui l'ont notée », pour la recherche ciblée sur les
      entités rares.
  - *Sa faiblesse connue.* Un attaquant qui crée beaucoup de nœuds peut se
    placer autour d'une clé et contrôler les réponses : ce sont les attaques
    Sybil et l'éclipse. Chez nous, les signatures ramènent ce risque à de
    l'omission : il ne peut pas forger de note, ni de chaîne de confiance
    vers le lecteur. Il peut seulement cacher des résultats. On limite ce
    risque en interrogeant plusieurs chemins, et en combinant la DHT avec
    les copies détenues par ses propres contacts.
  - *Ce que la confiance y ajoute.* La recherche est neutre, mais l'ordre
    des recherches ne l'est pas : on cherche d'abord les notes des auteurs
    les plus connectés au lecteur, qui pèsent le plus dans l'estimateur.
  - *Ce que ce n'est pas.* Nos idées de réplication locale ne réinventent
    pas Kademlia. Elles suivent le graphe social, ce qui les rapproche de
    Scuttlebutt. Les deux mécanismes sont complémentaires.
- **Nostr**, **AT Protocol** (Bluesky), **ActivityPub** : comme transports
  possibles, ou pour vérifier en quoi on se différencie.
