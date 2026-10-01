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

## Où sont les données

Tout est dans le dossier `data/` à la racine du projet. Il n'est pas versionné.

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
