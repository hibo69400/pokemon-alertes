# Alertes Pokémon 30 ans

Surveille des pages produit, des prospectus **et la veille communautaire** (presse, Dealabs,
Reddit, Telegram, Bluesky…), et t'envoie une notification sur ton téléphone Android
(via l'app gratuite **ntfy**) dès qu'un produit est en stock, qu'un prospectus parle de Pokémon
ou qu'un post annonce un restock.

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

## Veille communautaire (section `veille` de `config.yml`)

Trois types de sources, sans clé API :

| type       | sert à lire…                                   | exemples                                   |
|------------|------------------------------------------------|--------------------------------------------|
| `rss`      | un flux RSS / Atom                             | Google Actualités, Reddit, Bluesky, blogs  |
| `telegram` | un canal Telegram **public** (`t.me/s/nom`)    | canaux d'alertes restock                   |
| `page`     | une page web quelconque (nouvelles lignes)     | un fil de forum, Dealabs                   |

Fonctionnement :
- 1er passage d'une source : le script mémorise l'existant **sans alerter**, puis envoie un seul
  message « Veille activée ».
- Ensuite, chaque nouveau post qui parle de Pokémon **et** de restock (mots-clés réglables) donne une
  notification, avec le lien. Un post mentionnant le 30e anniversaire passe en priorité maximale.
- La même info vue sur plusieurs sources ne donne qu'une alerte. Les annonces de vente / échange
  entre particuliers sont ignorées.
- Plus de 3 alertes d'un coup sur une source : un seul message groupé.
- Source en panne 3 passages de suite (~45 min) : une alerte « source illisible », une seule fois.

**Tester une source avant de s'y fier** (aucune notification, rien de sauvegardé) :

```
python watcher.py --veille-test
```

Sur GitHub, tu peux aussi lire les logs de l'onglet Actions après un *Run workflow*.

## Limites à connaître

- **Discord** : impossible à lire sans y installer un bot, ce qui demande les droits d'administrateur
  du serveur. Garde l'app Discord avec les notifications activées pour ces serveurs.
- **X / Twitter** : pas de flux gratuit et stable (voir l'exemple commenté dans `config.yml`).
- **Reddit** bloque parfois les serveurs GitHub (erreur 403).
- **Délai** : GitHub Actions lance le script toutes les ~15 min (parfois avec du retard). Pour un
  restock qui part en quelques minutes, une alerte de communauté arrive souvent après la bataille :
  les vérifications directes de stock des magasins restent les plus rapides.
