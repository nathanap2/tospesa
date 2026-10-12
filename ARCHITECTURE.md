# tospesa — architecture réseau

La première partie décrit ce qui est en place. La suite rassemble les pistes
encore ouvertes. Les features utilisateur sont dans `FEATURES.md`.

## Ce qui est en place

- **Enregistrements signés** (`tospesa/protocol.py`), en Ed25519. Chacun se
  vérifie seul, donc il peut transiter par n'importe quel pair. Deux types :
  - les *notes*, sur l'empreinte SHA-256 d'une entité ;
  - les *annonces* : la liste des adresses où trouver les notes de leur
    auteur. Le format des adresses est contraint, et leur nombre limité, pour
    ne pas ouvrir un champ de texte libre.
- **Copie locale** (`tospesa/store.py`). La fusion ne dépend ni de l'ordre
  d'arrivée ni des doublons. Deux copies qui ont reçu les mêmes
  enregistrements, par n'importe quels chemins, sont identiques. Elle est
  persistée dans un journal où l'on ne fait qu'ajouter, partagé sous verrou
  entre le nœud qui tourne et la ligne de commande. Chaque processus relit ce
  que l'autre y ajoute.
- **Adresses et chiffrement.** L'adresse d'un nœud contient la clé publique
  de son propriétaire : `tsp://<clé>@hôte:port`. Les échanges sont chiffrés en
  TLS 1.3, avec un certificat autosigné dérivé de cette clé. Le client vérifie
  que le nœud joint présente bien la clé de l'adresse, avant de lui envoyer
  quoi que ce soit. Sinon, l'adresse est signalée comme imposteur. Il n'y a
  pas d'autorité de certification : c'est la clé qui fait foi, comme dans
  Scuttlebutt.
- **Nœuds** (`tospesa/node.py`). Le même programme sert sa copie en HTTPS
  (`/heads`, `/push`, `/pull`, `/trace`), avec deux politiques d'accueil :
  - le *nœud personnel* (`tospesa node`) n'accepte que les enregistrements
    de son voisinage de confiance. Comme sa copie contient ce voisinage, ses
    contacts y retrouvent les notes de tiers absents : ce sont les copies de
    secours ;
  - le *relais* (`tospesa relay`) accepte tout enregistrement correctement
    signé. C'est une première version des facilitateurs de réseau. Un
    opérateur pourra y ajouter ses règles (quotas, clés admises…).
- **Synchronisation** (`tospesa sync`, et périodiquement dans le nœud
  personnel). On joint tous les pairs connus : ceux de sa configuration
  (`tospesa peers add`), puis les adresses annoncées par son voisinage, les
  plus proches d'abord. Ceux qui ne répondent pas sont passés. On publie ses
  enregistrements, puis on récupère ceux de son voisinage. Son annonce liste
  son nœud et les pairs qui ont accepté ses notes (modèle « outbox » de
  Nostr) : ses contacts savent où les trouver même quand son nœud est éteint.
- **Recherche ciblée** (`/trace`). « Dans le réseau de confiance de Bob, qui a
  noté X ? » Le pair répond avec les chaînes de notes signées qui relient Bob
  à ces auteurs, et rien d'autre.
- **Estimateur incomplet** (`tospesa score`). Il affiche d'abord le score de
  la copie locale, avec sa certitude, puis le score affiné après la recherche
  ciblée auprès des pairs.
- **Défense en profondeur.** Le client ignore ce qu'un pair renvoie sans
  qu'on l'ait demandé, et écarte les enregistrements invalides. Un pair
  malveillant ne peut donc ni injecter de notes d'inconnus dans la copie
  locale, ni falsifier de notes. Il peut seulement en retenir.
- **Échange manuel** (`export` / `import`) : une réplication par fichier. Un
  fichier importé est traité comme un pair.

### Travail restant
- Les machines derrière une box ne sont pas joignables. Elles passent par
  les relais. Le contournement des box (NAT traversal) est un chantier à
  part.
- Un pair voit les requêtes qu'on lui adresse : qui consulte, et quelles
  empreintes. Le chiffrement protège ces requêtes des observateurs du réseau,
  pas du pair lui-même. Cela rejoint la question ouverte de la privacy.
- Le calcul du score parcourt toute la copie locale à chaque consultation. Il
  faudra l'optimiser pour de grands voisinages.
- Les entités notées positivement (livres, etc.) sont aussi demandées aux
  pairs comme si c'étaient des auteurs, puisque tout est empreinte. Ces
  demandes ne renvoient rien et coûtent un peu de bande passante.

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
