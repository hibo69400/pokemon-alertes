# ─────────────────────────────────────────────────────────────
#  CONFIGURATION — Alertes Pokémon 30 ans
# ─────────────────────────────────────────────────────────────

ville: "Villefranche-sur-Saône"   # sert d'étiquette dans les notifications

# Un texte est lié à Pokémon s'il contient un de ces mots (accents/majuscules ignorés)
mots_cles: ["pokemon"]

# Mots qui signalent le 30e anniversaire dans un prospectus
mots_cles_30ans: ["30 ans", "30th", "30e anniversaire", "trentieme"]

# Mots qui signalent un produit 30 ans dans une page de collection (nom ou adresse du lien)
mots_cles_collections: ["30 ans", "30th", "30e", "30eme", "anniversaire", "celebration"]

# Lecture des prospectus en images par IA (nécessite le secret ANTHROPIC_API_KEY)
modele_ia: "claude-haiku-4-5-20251001"
max_pages_ia: 40                # limite de pages lues par prospectus (contrôle le coût)
max_produits_par_page: 40       # limite de produits vérifiés par page de collection

# ── 1) PAGES DE COLLECTION ───────────────────────────────────
# Une adresse par enseigne : la page qui liste les produits Pokémon 30 ans.
# Le script te prévient : d'un NOUVEAU produit 30 ans, et d'un retour EN STOCK.
# Comment trouver l'adresse : sur le site de l'enseigne, cherche « Pokémon 30 ans »,
# ouvre la page de résultats ou de collection, copie l'adresse de la barre du navigateur.
# « deja_pokemon: true » = la page ne contient que du Pokémon (les noms des produits
# peuvent alors ne pas contenir le mot « Pokémon »).
collections:
  - nom: "La Grande Récré"
    url: "https://www.lagranderecre.fr/evenements/30-ans-pokemon.html"
    deja_pokemon: true

  # À décommenter et compléter avec les adresses trouvées (enlève le # devant chaque ligne) :
  # - nom: "King Jouet"
  #   url: "COLLE ICI L'ADRESSE DE LA PAGE POKÉMON 30 ANS"
  # - nom: "JouéClub"
  #   url: "COLLE ICI L'ADRESSE"
  # - nom: "Micromania"
  #   url: "COLLE ICI L'ADRESSE"
  # - nom: "Cultura"
  #   url: "COLLE ICI L'ADRESSE"
  # - nom: "E.Leclerc"
  #   url: "COLLE ICI L'ADRESSE"
  # - nom: "Carrefour"
  #   url: "COLLE ICI L'ADRESSE"
  # - nom: "Auchan"
  #   url: "COLLE ICI L'ADRESSE"

# ── 2) PRODUITS PRÉCIS (facultatif) ──────────────────────────
# Pour suivre une page produit en particulier (une ligne par produit).
produits: []
  # - nom: "Coffret Dresseur d'Élite 30e Anniversaire"
  #   enseigne: "King Jouet"
  #   url: "ADRESSE EXACTE DE LA PAGE DU PRODUIT"

# ── 3) PROSPECTUS ────────────────────────────────────────────
# Adresse d'un PDF, d'une image ou d'une page web de prospectus.
# Chaque nouvelle version est analysée une seule fois.
prospectus: []
  # - nom: "Prospectus Carrefour"
  #   url: "ADRESSE DU PDF OU DE LA PAGE DU PROSPECTUS"
