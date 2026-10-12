# tospesa — spécifications fonctionnelles

## Vision

Un outil pour savoir en quoi on peut avoir confiance : des personnes, des sites,
des livres, des entreprises, des restaurants, une modification d'un article
Wikipédia… à peu près n'importe quoi.

La confiance est **subjective**. Le score d'une entité n'est pas le même pour
tout le monde : il est calculé depuis la position de celui qui consulte, à
partir des notes des entités en qui il a confiance, directement ou de proche
en proche. C'est le principe même du produit.

## Les trois couches

1. **Le protocole** (couche fondamentale) : des utilisateurs posent des notes
   sur des entités. C'est tout. Le protocole *permet* de construire des
   réseaux de confiance et de défiance, sans juger de ce qui y circule. En
   théorie, le réseau peut contenir n'importe quoi.
2. **Le calcul du score** : transformer les notes en un score personnel. On en
   fournit un algorithme de référence. Comme les notes sont publiques et
   vérifiables, chaque client peut en utiliser un autre.
3. **Les outils côté client** : tout ce qu'une interface fait pour son
   utilisateur au-dessus des deux premières couches. Par exemple :
   l'onboarding, les configurations par défaut, les garde-fous, ou noter
   automatiquement à l'installation des nœuds qui insèrent l'utilisateur dans
   un cadre « officiel ». C'est la responsabilité de l'utilisateur et de son
   interface : chacun utilise ce qu'il veut, ou rien.

Le modèle est celui de LBRY et Odysee : d'abord le réseau, puis par-dessus des
interfaces qui en donnent un accès « normal ».

On commence par les deux premières couches.

## 1. Le protocole

### Concepts
- **Entité** : tout ce qui peut être noté. Elle est désignée par un
  identifiant qui la distingue sans ambiguïté (de préférence à son nom). Le
  réseau ne stocke et ne transmet que l'**empreinte** (hash) de cet
  identifiant, jamais la chaîne elle-même. Personne ne peut ainsi injecter de
  contenu dans les copies que les utilisateurs gardent les uns des autres.
  Pour consulter une entité, le client calcule son empreinte. Pour découvrir
  ce que son réseau a noté, il calcule les empreintes d'un catalogue
  (bibliothèque, liste de sites…) et cherche celles qui ont été notées.
- **Utilisateur** : une entité comme les autres (on peut la noter), avec une
  spécificité : il pose des notes sur d'autres entités.
- **Note** : une déclaration d'un utilisateur sur une entité, sous forme d'un
  couple (valeur, pondération). La valeur exprime de la confiance (« je suis
  raccord ») ou de la défiance (« je ne suis pas raccord »). La pondération
  dit à quel point l'auteur engage sa note.
- **Réseau** : l'ensemble des notes posées par tous les utilisateurs.

### Noter une entité
Un utilisateur pose une note sur n'importe quelle entité, désignée par son
identifiant. Il peut modifier ou retirer sa note ensuite.

## 2. Le calcul du score (algorithme de référence)

### Score personnel
Un utilisateur consulte une entité, qu'il l'ait notée lui-même ou non, et
obtient un score qui lui est propre. Ce score dérive :
- de ses propres notes ;
- des notes des entités en qui il a confiance, de proche en proche, avec une
  influence qui diminue quand la distance augmente.

Une entité sur laquelle aucune information ne lui parvient a un score neutre.

### Délégation de confiance
Faire confiance à un utilisateur revient à hériter, en partie, de ses
jugements. Exemple de référence :

> Je fais confiance à un médecin. Ce médecin note positivement des articles
> scientifiques. Ces articles ont alors un score positif pour moi.
>
> Un nœud du réseau se charge de noter positivement des personnes dont il a
> vérifié qu'elles sont médecins. Si je donne la note maximale à ce nœud, j'ai
> immédiatement un biais positif sur tout ce que la médecine « officielle »
> accrédite.

Ce mécanisme sert aussi à créer des **nœuds accréditeurs** (ordres
professionnels, institutions, curateurs…). Personne n'est obligé de s'y fier :
l'outil ne censure rien, chacun choisit à qui il délègue.

### Règle de propagation
On n'écoute que ceux en qui on a confiance, mais on écoute tout ce qu'ils
disent, positif comme négatif. Pour A qui consulte :
- A fait confiance à B, B se méfie de C → C est négatif pour A ;
- A se méfie de B, B fait confiance à X → X n'est pas affecté (neutre) ;
- A se méfie de B, B se méfie de Y → Y n'est pas affecté (neutre).

Un lien de défiance pèse sur l'entité visée. Il ne transmet rien au-delà.

Le score se construit de proche en proche, comme une requête qui descend
dans mon réseau en connaissant sa profondeur. Pour me donner un avis, je
demande à chacun de mes contacts de confiance ce que pense *son* réseau. Je
fais ensuite la moyenne de leurs réponses, pondérée par ma confiance en
chacun et par leur certitude.

La certitude d'une réponse dépend de la proximité de sa meilleure source : son
poids est divisé par deux à chaque pas. Au-delà de 7 pas, une source ne pèse
plus que 0,5 % et on arrête de chercher. Le réseau derrière un contact, même
piraté, ne pèse donc pas plus que ce contact, à la proximité de leurs sources
près.

Chaque réponse indique la profondeur de sa meilleure source, pour qu'on sache
si l'estimation est solide ou lointaine.

Pas d'écho : quand je consulte, on fait comme si personne ne me faisait
confiance. Mon propre avis ne peut pas me revenir déguisé en avis d'un autre,
quelle que soit la longueur de la boucle. Que mes contacts me fassent
confiance en retour ne change pas mon score.

Quand on a noté soi-même une entité, son avis est sa note : son réseau ne la
modifie pas. *(À revoir avec les pondérations, voir les questions ouvertes.)*

### Résistance aux faux comptes
Des comptes que personne de mon réseau de confiance ne note positivement n'ont
aucune influence sur mes scores, même s'ils sont nombreux et se notent entre
eux.

## 3. Outils côté client

### Interface minimale
Pour commencer, une interface très simple : noter une entité et consulter un
score. Les supports d'accès plus riches viendront plus tard.

### Affichage du score et de sa confiance
La valeur d'un score ne suffit pas : un −1,00 tiré d'une seule source à 7 pas
est instable, et sa certitude est infime. L'interface doit :
- matérialiser la confiance du score d'une façon ou d'une autre (finesse
  d'affichage, indication de la profondeur de la meilleure source…) ;
- ne rien afficher (« pas assez d'information ») en dessous d'un seuil de
  certitude.

Ce seuil est le seul paramètre à régler. La profondeur de coupure du calcul
en découle, puisqu'il est inutile de chercher des sources dont la certitude
maximale resterait sous le seuil : 3 % → 5 pas, 1,5 % → 6 pas, 0,5 % → 7 pas
(valeur actuelle, à resserrer quand on construira l'affichage).

### Contacts et invitations
Un carnet de contacts local, jamais publié, permet de désigner une personne
par son nom (`@alice`) plutôt que par sa clé. On rejoint un ami avec son code
d'invitation : il entre dans le carnet, on lui fait confiance, et ses adresses
servent de points d'entrée dans le réseau.

### Désignation des entités
Une URL est normalisée avant d'être hachée, pour que ses différentes
écritures désignent la même entité. Toute autre chaîne est hachée telle
quelle, puisqu'on veut pouvoir noter des chaînes exactes (une modification
Wikipédia, par exemple) : c'est aux utilisateurs de s'accorder sur une
écriture pour les autres types (ISBN, lieux…).

### Note rapide
Depuis une modale, l'utilisateur note en un geste. La pondération vaut alors 1
par défaut.

### Affinités (à l'étude, nom provisoire)
Accélère l'onboarding. Quand je note une entité, mon client pose des notes de
**faible pondération** sur les autres notateurs de cette entité. La confiance
monte envers ceux qui sont d'accord avec moi et baisse envers ceux qui ne le
sont pas. Ces notes sont des notes comme les autres et se propagent
normalement.

On privilégie les notateurs **déjà reliés à mon réseau**, en particulier ceux
que mes contacts de confiance notent bien. L'affinité sert alors à valider ou
invalider rapidement les amis de mes amis, qui peuvent devenir mes amis (ou
pas). C'est ce qui oriente le mécanisme vers des entités réelles plutôt que
vers des faux comptes.

Ce dont l'outil a besoin côté moteur : trouver, parmi les gens de mon réseau
de confiance, ceux qui ont noté l'entité que je suis en train de noter, du
plus proche au plus lointain. Les résultats arrivent au fur et à mesure, pour
que l'interface puisse s'arrêter quand elle veut. Le dosage des pondérations
(assez petites, nombre limité, révision des « poussières ») relève de
l'interface.

Piste pour plus tard : le même filtrage collaboratif pourrait aussi ajuster la
pondération des notes de confiance déjà posées. Un contact avec qui je suis
souvent en désaccord verrait sa pondération baisser, et inversement.

Risque identifié : des comptes « caméléons » qui copient les avis consensuels
pour gagner de la confiance par affinité (*shilling attack*). La parade doit
découler du modèle lui-même, pas de rustines qui le cassent.

## Questions ouvertes

- **Pondération de sa propre note.** Si je note avec une faible pondération
  (« j'ai un vague avis »), mon réseau doit-il pouvoir encore nuancer mon
  score ? Une piste : ma note entre dans la moyenne comme une source parmi
  les autres, avec sa pondération.
- **Empreintes des identifiants.**
  - *Forme canonique.* Il faut normaliser avant de calculer l'empreinte
    (casse, URL, ISBN sans tirets…), une forme par type d'entité. Deux
    variantes d'une même entité donnent des empreintes sans rapport, et on
    ne peut plus les rapprocher après coup.
  - *Injection résiduelle.* Rien ne prouve qu'une empreinte en est une. On
    peut publier des morceaux de 32 octets d'un fichier à la place
    d'empreintes. C'est coûteux (une note par morceau) et limité aux copies
    faites par ceux qui font confiance à l'auteur.
  - *Pas de confidentialité.* L'empreinte d'un identifiant devinable se
    retrouve en essayant des candidats. Elle ne rend donc pas une note
    privée.
- **Boussole inversée (feature future).** Déclarer qu'une entité « indique le
  sud » : on suit ses jugements à l'envers. Ce qu'elle approuve devient
  négatif, ce qu'elle désapprouve devient positif. C'est un geste distinct de
  la simple défiance, qui, elle, ne transmet rien.
- **Privacy.** Une note peut-elle être privée tout en contribuant aux scores
  des autres ? Ce serait une feature intéressante. Il faut d'abord vérifier
  que ce n'est pas un obstacle technique rédhibitoire.
- **Couche réseau.** Les pistes (décentralisation, réplication, calcul,
  indexeurs, existant) sont dans `ARCHITECTURE.md`.
- **Échelle de note.** Question secondaire, à trancher à l'implémentation.
- **Explication du score.** Elle est secondaire pour l'instant : le score
  suffit. On pourra ajouter plus tard une couche d'explication (« qui a
  contribué à ce score »).
