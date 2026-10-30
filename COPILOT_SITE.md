# DarkCodex — Mise en place du site officiel (consignes pour GitHub Copilot)

## 1. Objectif

Le site actuel ne contient que quatre cartes (Connexion, Abonnements, Compte, Terminal). Il manque une vraie présentation, les guides d’installation et d’utilisation, la page des tarifs et la FAQ. Les pages sont **déjà écrites et testées** : elles sont dans le dossier `web/` de l’archive `darkcodex-site.zip`. Ta mission : les intégrer au dépôt et les brancher au serveur.

## 2. Les pages livrées

| Fichier | Adresse | Contenu |
| --- | --- | --- |
| `web/index.html` | `/` | Présentation, fonctionnalités, comment ça marche, aperçu des tarifs (chargé depuis `/plans`) |
| `web/installation.html` | `/installation` | Guide complet : Termux, Linux, Windows, connexion du compte, vérification, mise à jour, dépannage |
| `web/utilisation.html` | `/utilisation` | Guide complet : modes, toutes les commandes du chat, tokens, parrainage, commandes hors chat, conseils |
| `web/abonnements.html` | `/abonnements` | **Remplace** l’actuel. Tarifs visibles sans connexion ; la connexion n’est demandée qu’au moment d’acheter |
| `web/faq.html` | `/faq` | Questions fréquentes |
| `web/statut.html` | `/statut` | État du service (lit `/health` et `/version`) |
| `web/legal.html` | `/legal` | Conditions d’utilisation, achats et tokens, confidentialité |
| `web/site.css` | `/static/site.css` | **Remplace** l’actuel (mêmes classes + nouvelles) |
| `web/nav.js` | `/static/nav.js` | Nouveau : en-tête, pied de page et boutons « Copier », injectés sur chaque page |

Ne modifie **pas** le contenu des pages. Si tu vois une erreur, signale-la au lieu de la corriger toi-même.

## 3. Étapes d’intégration

### 3.1 Copier les fichiers

Copie les 9 fichiers du tableau dans `web/`. Remplace `site.css` et `abonnements.html`. Ne touche pas à `auth.js`, `connexion.html`, `compte.html`, `device.html` ni `retour.html` (voir 3.3).

### 3.2 Brancher les routes dans server/main.py

Dans le dictionnaire `PAGES`, ajoute les 5 nouvelles pages :

**Fichier : `server/main.py — dictionnaire PAGES`**

```
PAGES = {
    "/device": "device.html",
    "/connexion": "connexion.html",
    "/abonnements": "abonnements.html",
    "/compte": "compte.html",
    "/paiement/retour": "retour.html",
    "/installation": "installation.html",   # nouveau
    "/utilisation": "utilisation.html",     # nouveau
    "/faq": "faq.html",                     # nouveau
    "/statut": "statut.html",               # nouveau
    "/legal": "legal.html",                 # nouveau
}
```

Remplace la route racine. **Attention :** le dépôt déployé sur Render est plus récent que l’archive analysée (commit « Fix the root HTML route… »). Cherche donc la route `/` **actuelle**, quelle que soit sa forme (JSON ou page HTML écrite dans le code), supprime-la et mets à la place :

**Fichier : `server/main.py — route racine`**

```
@app.get("/", include_in_schema=False)
def racine() -> FileResponse:
    """Page d'accueil du site officiel."""
    return _servir_page("index.html")
```

- La route `/` ne doit exister **qu’une seule fois** (la première déclarée gagne).
- Les routes `/plans`, `/health` et `/version` doivent rester telles quelles. Si `/health` n’existe pas, ce n’est pas grave : `statut.html` se rabat sur `/version`.

### 3.3 Ajouter l’en-tête commun aux pages existantes

Dans `connexion.html`, `compte.html`, `device.html` et `retour.html`, ajoute cette ligne juste avant `</head>` (sans rien changer d’autre) :

```
  <script src="/static/nav.js" defer></script>
```

Commande possible : `sed -i 's#</head>#  <script src="/static/nav.js" defer></script>\n</head>#' web/connexion.html web/compte.html web/device.html web/retour.html` (vérifie qu’elle n’est pas ajoutée deux fois).

### 3.4 Vérifier les dépendances de abonnements.html

La nouvelle page importe depuis `/static/auth.js` : `getSession`, `apiFetch` et `memoriserRetour`. Vérifie qu’ils sont bien exportés par le `auth.js` du dépôt (c’est le cas dans la version analysée). La logique de paiement (`/paiement/creer`, `/confirmer-paiement`, retour SasPay) est conservée à l’identique.

## 4. Tests à ajouter (tests/test_site.py)

- Chaque adresse `/`, `/installation`, `/utilisation`, `/abonnements`, `/faq`, `/statut`, `/legal`, `/connexion`, `/compte`, `/device`, `/paiement/retour` répond 200 avec `text/html`.
- `GET /` renvoie du HTML contenant « Un assistant IA de codage » (et non du JSON).
- `/static/nav.js` et `/static/site.css` répondent 200.
- Chaque page publique contient `<script src="/static/nav.js"`.
- `abonnements.html` ne contient plus d’appel bloquant à `exigerConnexion` (la page est publique).
- Mettre à jour tout test existant qui attendait le JSON `{"service": "DarkCodex API", ...}` sur `/`.
Lance ensuite `python -m pytest` et indique le résultat.

## 5. Ce que Copilot ne doit pas faire

- Ne pas réécrire les textes des pages ni ajouter de promesses (par exemple « sans restrictions »).
- Ne pas ajouter de clés, d’adresses email ou de numéros dans les pages.
- Ne pas retirer les `<script type="module">` ni les attributs de sécurité (le contenu venant du serveur est inséré avec `textContent`, jamais `innerHTML`).

## 6. Les tarifs affichés viennent de Supabase

Les cartes de prix de `/` et de `/abonnements` sont chargées depuis `GET /plans`, donc depuis la table `plans`. Si la page affiche « Tarifs momentanément indisponibles », la cause est côté serveur : variables `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` manquantes sur Render, ou scripts SQL non lancés. Aucun prix n’est écrit en dur dans les pages.

## 7. Vérification finale (Moussa)

|  | Contrôle |
| --- | --- |
| ☐ | Les 7 nouvelles adresses s’ouvrent (`/installation`, `/utilisation`, `/faq`, `/statut`, `/legal`, `/`, `/abonnements`) |
| ☐ | Le menu en haut s’affiche sur toutes les pages, y compris `/connexion` et `/compte` |
| ☐ | `/abonnements` affiche gratuit, starter et pro **sans être connecté** |
| ☐ | Le bouton « Se connecter pour acheter » mène à `/connexion`, puis ramène sur `/abonnements` |
| ☐ | Sur `/installation`, le bouton « Copier » fonctionne sur les blocs de commandes |
| ☐ | Le dépôt GitHub `moussaram/DarkCodex` est **public** (sinon `git clone` échouera pour les utilisateurs) |
| ☐ | Tu as relu `/legal` et ajusté ce qui ne correspond pas à ta politique (remboursement, contact) |

