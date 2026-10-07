# StuffManager

Inventaire de la maison pour retrouver tous vos objets : meubles, livres, vêtements, ustensiles, outils, électronique… L'application tourne sur un Raspberry Pi, sur le réseau local : une seule personne, pas de compte utilisateur.

L'interface est en français et se lit sur un téléphone, dans toute la maison.

## Ce que fait l'application

- Une fiche par objet : nom, catégorie, quantité, emplacement, niveau d'utilisation, photo, notes, référence. Une catégorie peut activer le suivi d'utilisation : on note alors combien sont utilisés, et le disponible est la quantité moins les utilisés.
- Recherche par nom, référence ou code, et filtres par catégorie, emplacement ou niveau d'utilisation.
- Les emplacements (tiroir, boîte, étagère…) listent tout ce qui est rangé au même endroit. On peut en ajouter, les renommer, et supprimer ceux qui sont vides.
- Même chose pour les catégories, tant qu'aucun objet ne les utilise. Le suivi d'utilisation se règle catégorie par catégorie, et il est coupé par défaut.
- Une étiquette imprimable : nom, catégorie, emplacement, code-barres, code lisible et QR.

Catégories de départ : Cuisine, Mobilier, Décoration, Linge, Vêtement, Livre, Jeu, Jardin, Câble, Électronique, Connectique, Outil, Consommable, Autre. Elles ne sont pas verrouillées.

Niveaux d'utilisation : Jamais, Rare, Occasionnel, Fréquent.

L'emplacement peut rester vide le temps de cataloguer un objet. La quantité de départ proposée est 1, le niveau « Occasionnel ».

## Lancer sur un Raspberry Pi avec Docker

Le Pi doit être en 64 bits (arm64), avec Docker et Compose. Le téléphone doit être sur le même réseau.

```bash
git clone https://github.com/maximeWasilewski/StuffManager.git
cd StuffManager
mkdir -p data
docker compose up -d --build
```

Trouver l'adresse du Pi :

```bash
hostname -I
```

Ouvrir `http://<adresse-ip-du-pi>:8080`.

Arrêter :

```bash
docker compose down
```

Le conteneur publie le port **8080**. `restart: unless-stopped` le relance après un redémarrage du Pi, si le service Docker démarre avec la machine.

Mettre à jour :

```bash
git pull
docker compose up -d --build
```

Journaux : `docker compose logs -f`.

## Installer sur Home Assistant OS

Home Assistant OS ne lance pas ce `docker-compose.yml`. StuffManager s'installe comme module complémentaire local. Le dossier à copier est `addon` (il contient `config.yaml` à sa racine).

1. Récupérez ce dépôt, puis copiez le dossier `addon` dans le partage Samba `addons` du Pi (module « Samba share », ou le dossier `/addons` via SSH). Le chemin attendu est `addons/addon/config.yaml`.
2. Dans Home Assistant : Paramètres → Modules complémentaires → Boutique → menu (⋮) → Recharger.
3. Sous « Modules complémentaires locaux », ouvrez StuffManager, installez-le, puis démarrez-le. Le démarrage au boot est automatique. Après avoir remplacé le dossier `addon`, rechargez la boutique puis utilisez Reconstruire sur la fiche du module : un redémarrage relance l'image déjà construite.
4. Ouvrez `http://<ip-du-pi>:8080` depuis le téléphone. Il n'y a pas d'ingress : l'interface n'est pas intégrée au menu de Home Assistant. Le port publié est 8080 sur l'hôte. Si ce port est déjà pris, changez seulement le port hôte dans la configuration réseau du module ; le conteneur reste sur 8080, et le téléphone doit utiliser le port hôte choisi.

Les données de l'add-on sont dans le volume persistant `/data` du conteneur, pas dans le `./data` de Docker Compose :

- `/data/stuffmanager.db`
- `/data/photos`

Elles restent en place si vous reconstruisez ou mettez à jour le module. Une sauvegarde Home Assistant de ce module est faite à froid (le module est arrêté le temps de la copie, pour que SQLite soit cohérent).

Plus tard, le même dépôt GitHub pourra être ajouté comme dépôt de modules : il contient `repository.json` à la racine. L'URL à coller dans la boutique est `https://github.com/maximeWasilewski/StuffManager`.

## Ajout rapide par photo (1.4.0)

Ouvrez **Ajout rapide** dans la navigation. Prenez une photo de l'objet ou choisissez
une image dans la galerie ; pour un câble, montrez les deux connecteurs côte à côte.
La proposition d'identification reste à vérifier : une photo ne permet pas de déduire
une puissance, une vitesse de transfert ou une longueur non inscrite sur le câble.

StuffManager recherche ensuite des fiches similaires dans tout l'inventaire, par
leurs noms, références et connecteurs connus. Il écarte les extrémités explicitement
incompatibles. Cette recherche est une proposition, pas une garantie de doublon :
vérifiez notamment la longueur, les connecteurs mâles/femelles et la référence.
Le champ de recherche permet de corriger l'identification ou de retrouver une fiche
nommée différemment. Jusqu'à 12 propositions sont affichées.

- **Déjà en stock** : choisissez la fiche et la quantité à ajouter. Le rangement
  exact (emplacement et détail), la photo existante et le nombre d'objets utilisés
  restent en place ; les nouveaux objets sont disponibles.
- **Nouvel objet** : corrigez le nom, la catégorie et l'emplacement, puis confirmez.
  La photo est conservée sur la nouvelle fiche.
- **Sans clé ou analyse indisponible** : la photo reste utilisable et le parcours
  fonctionne en identification manuelle.

Aucun objet n'est créé ni aucune quantité modifiée pendant l'analyse. Une double
confirmation ne compte qu'une fois. Si le stock ou le rangement change entre la
proposition et la confirmation, l'application demande de vérifier à nouveau.

### Activer la reconnaissance dans Home Assistant

Après fusion de la version 1.4.0 et mise à jour du module, ouvrez **Configuration** :

```yaml
openai_api_key: "votre-cle-API-OpenAI"
openai_model: "gpt-4.1-mini"
```

Enregistrez, puis redémarrez le module. La clé est lue côté serveur depuis
`/data/options.json`. Elle n'est jamais insérée dans les pages web ni dans
la base d'inventaire. Laissez la clé vide pour utiliser l'ajout manuel.

La reconnaissance utilise l'[API OpenAI pour les images](https://developers.openai.com/api/docs/guides/images-vision).
Elle nécessite une clé API et une facturation API active ; elle ne se connecte pas
à votre session ChatGPT. Les appels peuvent occasionner des frais sur le compte API.
Seules la photo convertie en JPEG et la liste des noms de catégories sont envoyées
à OpenAI. Les noms et références du stock sont comparés localement. Les réponses
ne sont pas enregistrées chez OpenAI via l'option `store` (`store: false`) ; les
[règles de conservation de l'API](https://developers.openai.com/api/docs/guides/your-data)
restent applicables.

### Avec Docker Compose ou Python

Avec Compose, créez un fichier `.env` (ignoré par Git) à la racine :

```dotenv
OPENAI_API_KEY=votre-cle-API-OpenAI
OPENAI_VISION_MODEL=gpt-4.1-mini
```

Puis `docker compose up -d --build`. Sans Docker, définissez ces mêmes variables
d'environnement avant de lancer Uvicorn. Aucun nouveau service n'est nécessaire.

Les brouillons et photos temporaires sont dans la base SQLite. Ils expirent après
24 heures et sont nettoyés au prochain ajout de photo. À la confirmation, la photo
temporaire est retirée du brouillon ; seule une nouvelle fiche reçoit cette photo.
Les brouillons non terminés survivent à un redémarrage pendant leur durée de validité.

## Où sont les données

Avec Docker Compose, tout est dans le dossier `data/` à la racine du projet. Il n'est pas versionné. Sur Home Assistant OS, voir la section précédente : les mêmes fichiers sont sous `/data` dans le module.

- `data/stuffmanager.db` — base SQLite (objets, catégories, emplacements, compteur de codes)
- `data/photos/` — une photo JPEG par fiche

`docker-compose.yml` monte `./data` dans le conteneur. Supprimer le conteneur ne supprime pas ce dossier.

Pour une sauvegarde : arrêter l'application, puis copier `data/`.

Au démarrage, les tables manquantes sont créées, et les colonnes manquantes sont ajoutées à une base déjà en place (y compris `/data/stuffmanager.db` sur Home Assistant). Les catégories de départ ne sont ajoutées que si la table des catégories est vide.

## QR codes des lieux

Ouvrez **Emplacements**, choisissez un lieu, puis **QR code / Étiquette du lieu**.
Vous pouvez imprimer une étiquette avec son nom ou télécharger le QR code en PNG.
Le QR contient l'adresse complète du lieu (par exemple `http://192.168.1.20:8080/emplacements/3`).
Un scan avec l'appareil photo du téléphone ouvre directement sa page et tous les objets qui y sont rangés.

Créez l'étiquette en ouvrant StuffManager depuis l'adresse accessible au téléphone,
pas depuis `localhost`. Le téléphone doit pouvoir joindre le serveur, généralement sur le même réseau local.
Réservez l'adresse IP du Pi ou utilisez un nom réseau stable : si l'adresse ou le port change,
réimprimez les QR codes. Renommer un lieu ne change pas son lien ; après suppression,
son lien renvoie une page introuvable et son identifiant n'est jamais réattribué.

Les catégories, objets, photos et lieux existants sont conservés lors de la mise à jour.
Les nouvelles catégories de départ ne sont créées que pour un inventaire sans catégories ;
sur une installation existante, ajoutez celles qui vous intéressent dans **Catégories**.

## Codes-barres et QR des objets

À la création, chaque objet reçoit un code stable de la forme `SM-000001`. Le numéro suivant ne revient pas en arrière : une fiche supprimée ne libère pas son code, et l'adresse `/composants/{id}` n'est pas réutilisée.

- **Code 128** : image du code `SM-000001`. Avec une douchette qui saisit au clavier, taper dans la recherche (ou scanner, si le champ a le focus) : un code exact ouvre la fiche.
- **QR des objets** : il contient le chemin relatif `/composants/{id}` (par exemple `/composants/12`), pas l'adresse complète du Pi. Il identifie la fiche sur ce serveur. La recherche accepte aussi ce chemin. L'appareil photo du téléphone ne peut pas ouvrir une adresse relative tout seul.
- **Étiquette** : le bouton Imprimer ouvre la fenêtre d'impression du navigateur. L'aperçu est calé sur une largeur d'environ 62 mm. Décochez les en-têtes et pieds de page du navigateur pour un sticker plus propre.

## Photos

JPEG, PNG, WebP ou HEIC (iPhone), 8 Mo maximum à l'envoi. L'image est tournée selon les données du téléphone, convertie en JPEG (côté le plus long : 1600 pixels) et remplace la photo précédente. Une seule photo par fiche. Le formulaire montre un aperçu avant l'enregistrement.

## Lancer sans Docker

Python 3.12, depuis la racine du dépôt.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Les tests :

```bash
pytest
```
