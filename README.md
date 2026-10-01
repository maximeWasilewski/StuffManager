# StuffManager

Inventaire d'atelier pour retrouver composants électroniques, câbles, connectique et petit matériel. L'application tourne sur un Raspberry Pi, sur le réseau local : une seule personne, pas de compte utilisateur.

L'interface est en français et se lit sur un téléphone, dans l'atelier.

## Ce que fait l'application

- Une fiche par composant : nom, catégorie, quantité, emplacement, niveau d'utilisation, photo, notes, référence.
- Recherche par nom, référence ou code, et filtres par catégorie, emplacement ou niveau d'utilisation.
- Les emplacements (tiroir, boîte, étagère…) listent tout ce qui est rangé au même endroit. On peut en ajouter, les renommer, et supprimer ceux qui sont vides.
- Même chose pour les catégories, tant qu'aucun composant ne les utilise.
- Une étiquette imprimable : nom, catégorie, emplacement, code-barres, code lisible et QR.

Catégories de départ : Câble, Électronique, Connectique, Outil, Consommable, Autre. Elles ne sont pas verrouillées.

Niveaux d'utilisation : Jamais, Rare, Occasionnel, Fréquent.

L'emplacement peut rester vide le temps de cataloguer une pièce. La quantité de départ proposée est 1, le niveau « Occasionnel ».

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

## Où sont les données

Avec Docker Compose, tout est dans le dossier `data/` à la racine du projet. Il n'est pas versionné. Sur Home Assistant OS, voir la section précédente : les mêmes fichiers sont sous `/data` dans le module.

- `data/stuffmanager.db` — base SQLite (composants, catégories, emplacements, compteur de codes)
- `data/photos/` — une photo JPEG par fiche

`docker-compose.yml` monte `./data` dans le conteneur. Supprimer le conteneur ne supprime pas ce dossier.

Pour une sauvegarde : arrêter l'application, puis copier `data/`.

Au démarrage, les tables manquantes sont créées. Les six catégories de départ ne sont ajoutées que si la table des catégories est vide.

## Codes-barres et QR

À la création, chaque composant reçoit un code stable de la forme `SM-000001`. Le numéro suivant ne revient pas en arrière : une fiche supprimée ne libère pas son code, et l'adresse `/composants/{id}` n'est pas réutilisée.

- **Code 128** : image du code `SM-000001`. Avec une douchette qui saisit au clavier, taper dans la recherche (ou scanner, si le champ a le focus) : un code exact ouvre la fiche.
- **QR** : il contient le chemin relatif `/composants/{id}` (par exemple `/composants/12`), pas l'adresse complète du Pi. Il identifie la fiche sur ce serveur. La recherche accepte aussi ce chemin. L'appareil photo du téléphone ne peut pas ouvrir une adresse relative tout seul.
- **Étiquette** : le bouton Imprimer ouvre la fenêtre d'impression du navigateur. L'aperçu est calé sur une largeur d'environ 62 mm. Décochez les en-têtes et pieds de page du navigateur pour un sticker plus propre.

## Photos

JPEG, PNG ou WebP, 8 Mo maximum à l'envoi. L'image est tournée selon les données du téléphone, convertie en JPEG (côté le plus long : 1600 pixels) et remplace la photo précédente. Une seule photo par fiche.

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
