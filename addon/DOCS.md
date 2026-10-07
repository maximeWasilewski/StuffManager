# StuffManager — Ajout rapide

Prenez une photo depuis **Ajout rapide**, avec les deux extrémités visibles pour
un câble. Vérifiez l'identification, puis ajoutez une quantité à une fiche existante
et reprenez son emplacement, ou créez une nouvelle fiche avec la photo.

## Reconnaissance OpenAI

### Avec votre abonnement ChatGPT (1.5.0)

Dans **Ajout rapide → Connexion et réglages ChatGPT**, téléchargez l'assistant
sur un PC équipé de Python 3.12 ou plus. Extrayez le ZIP puis, dans ce dossier :

```shell
python -m pip install -r requirements.txt
python connect_chatgpt.py
```

Sous Windows, `py` peut remplacer `python`. Connectez-vous sur le site OpenAI
ouvert par l'assistant et autorisez le forfait ChatGPT. Puis importez le fichier
`connexion-stuffmanager.chatgpt` dans StuffManager, sous une heure. Ce fichier
est chiffré pour votre module ; aucun jeton n'est envoyé en clair au navigateur
StuffManager. Téléchargez l'assistant depuis votre instance de confiance.

Choisissez un modèle acceptant les images, puis **Mon abonnement ChatGPT** dans
l'ajout rapide. Un abonnement Plus ou Pro éligible est nécessaire ; les limites
du forfait s'appliquent. Le module renouvelle la connexion automatiquement.
La connexion n'accède pas à vos conversations ChatGPT. Vous pouvez la retirer
depuis cet écran et gérer l'utilisation depuis les réglages ChatGPT.

Le retour OpenAI est local au PC (`127.0.0.1`), ce qui explique cette étape sur
PC pour un module hébergé sur le Raspberry Pi. Elle ne se répète pas à chaque photo.

### Identification manuelle et photos

Le mode avec clé API séparée a été retiré. Sans compte ChatGPT connecté, ou en cas
d'erreur d'analyse, vous pouvez saisir le nom vous-même et continuer avec la photo.
Aucune quantité n'est modifiée avant confirmation. Les notes restent vides par défaut.

La photothèque accepte JPEG (y compris MPO), PNG, WebP, HEIC/HEIF, AVIF et TIFF,
jusqu'à 8 Mo. L'image principale est convertie en JPEG pour l'inventaire.

## Mise à jour

Le numéro du module est **1.5.2**. Depuis une installation du dépôt GitHub,
rechargez la boutique puis mettez à jour StuffManager. Pour une copie locale du
dossier `addon`, remplacez ce dossier et reconstruisez le module. Les données
d'inventaire restent dans `/data`.
