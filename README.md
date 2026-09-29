# Alertes Pokémon 30 ans

Surveille des pages produit et des prospectus, et t'envoie une notification
sur ton téléphone Android (via l'app gratuite **ntfy**) dès qu'un produit est en stock
ou qu'un prospectus parle de Pokémon.

## Installation

1. **Téléphone** : installe l'app *ntfy* (Play Store ou F-Droid), touche « + » et abonne-toi
   à un canal au nom secret et unique, par ex. `pokemon-k7x29qfm` (invente le tien).
   Autorise les notifications et désactive l'optimisation de batterie pour ntfy.
2. **GitHub** : crée un dépôt (repository) **public**, puis envoie-y tous ces fichiers
   (y compris le dossier `.github`).
3. Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**
   - `NTFY_TOPIC` = le nom de canal choisi à l'étape 1
   - `ANTHROPIC_API_KEY` = ta clé API (facultatif, pour lire les prospectus en images)
4. Onglet **Actions** → active les workflows → *Surveillance Pokémon* → **Run workflow**
   avec « test » coché : tu dois recevoir une notification sur ton téléphone.
5. Modifie `config.yml` (ta ville, les liens des produits et des prospectus).
   La surveillance tourne ensuite toute seule toutes les 15 minutes.

## Utilisation sur un ordinateur (sans GitHub)

```
pip install -r requirements.txt
export NTFY_TOPIC=pokemon-k7x29qfm
python watcher.py --test        # notification de test
python watcher.py --boucle 15   # surveille en continu
```
