# StuffManager — Ajout rapide

Prenez une photo depuis **Ajout rapide**, avec les deux extrémités visibles pour
un câble. Vérifiez l'identification, puis ajoutez une quantité à une fiche existante
et reprenez son emplacement, ou créez une nouvelle fiche avec la photo.

## Reconnaissance OpenAI

Dans la configuration de ce module, renseignez `openai_api_key`, enregistrez et
redémarrez le module. `openai_model` vaut `gpt-4.1-mini` par défaut ; vous pouvez
choisir un modèle compatible avec les images et les sorties structurées de l'API
Responses d'OpenAI.

Il faut une clé de l'API OpenAI avec une facturation active. La reconnaissance
n'utilise pas votre session ChatGPT et les appels sont facturés sur le compte API.
La photo et les noms de catégories sont envoyés à OpenAI ; les correspondances
avec le stock sont recherchées localement. La clé reste côté serveur. N'ajoutez
pas votre clé dans un ticket GitHub ou dans les photos.

Sans clé, ou en cas d'erreur d'analyse, vous pouvez saisir le nom vous-même et
continuer avec la photo. Aucune quantité n'est modifiée avant confirmation.

## Mise à jour

Le numéro du module est **1.4.0**. Depuis une installation du dépôt GitHub,
rechargez la boutique puis mettez à jour StuffManager. Pour une copie locale du
dossier `addon`, remplacez ce dossier et reconstruisez le module. Les données
d'inventaire restent dans `/data`.
