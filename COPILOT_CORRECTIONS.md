# DarkCodex — Corrections après analyse (consignes pour GitHub Copilot)

## Comment utiliser ce document

1. Lance **d’abord** le fichier SQL du chapitre 4 dans le SQL Editor de Supabase (c’est toi qui le fais, Copilot ne peut pas).
1. Donne ensuite ce document **en entier** à Copilot avec l’archive du projet.
1. Demande-lui de traiter les corrections **dans l’ordre du chapitre 3**, de lancer `python -m pytest` après chacune et de te dire ce qui a été fait.

# 1. Contexte et décisions prises

## 1.1 Où en est le projet

DarkCodex est un assistant IA en terminal (dossier `darkcodex/`) avec un serveur FastAPI hébergé sur Render (dossier `server/`), une base Supabase, Gemini côté serveur et le paiement SasPay. Un premier lot de corrections a déjà été appliqué. Une analyse complète de l’archive la plus récente (60 fichiers, 274 tests qui réussissent, 2 qui échouent) a montré ce qui reste à corriger.

## 1.2 Ce qui est déjà corrigé (ne pas toucher)

| Sujet | Où |
| --- | --- |
| Décompte des tokens atomique, plan remis à « gratuit » à 0 token | `debiter_profil` + fonction SQL `debiter_tokens` |
| `est_pro` : plan payant ET tokens restants | `darkcodex/cli.py` |
| Paiement : identifiant lu dans le webhook, rattrapage toutes les 10 min, `/confirmer-paiement` sans identifiant | `server/main.py`, `server/device.py` |
| Page `/abonnements` et pages `/connexion`, `/compte`, `/paiement/retour` | `web/` |
| Ancien système de licences (Moov Money) supprimé | `licence.py`, `generer_cle.py` supprimés |
| Jeton d’appareil `dkx_...` pour le terminal (seule son empreinte est stockée, révocable) | `server/device.py`, table `appareils` |
| Protections de paiement : prix lu côté serveur, revérification SasPay, montant/devise/propriétaire, crédit unique | `_traiter_paiement` |

## 1.3 Les décisions prises

- **Modèle : packs de tokens.** On achète un pack, on reçoit les tokens, sans date d’expiration et sans renouvellement automatique. Un abonnement mensuel pourra être ajouté plus tard, quand tout le reste sera stable.
- **Source unique des prix : la table `plans` de Supabase.** Le code ne contient plus de second catalogue de prix. Pour changer un prix, on modifie une ligne dans la table `plans`.
- **Trois plans** : `gratuit` (5 000 tokens, 0 FCFA), `starter` (50 000 tokens, 500 FCFA), `pro` (500 000 tokens, 2 500 FCFA). Les plans `business` et `enterprise` sont retirés pour l’instant. Pour en ajouter un plus tard, il suffit d’ajouter une ligne dans `plans`.
- **Le plan gratuit reste affiché** sur la page `/abonnements` (à titre d’information, sans bouton d’achat). C’est déjà le comportement actuel.
- **Le nom officiel du plan gratuit est `gratuit`** partout (et non `free`).
- **Le terminal utilise uniquement son jeton d’appareil `dkx_...`.** Plus de `refresh_token` Supabase côté terminal.
- **Domaine de production : `https://darkcodex-technicien.onrender.com`** (adresse affichée par Render pour le service `darkcodex-api`). Aucune adresse de serveur ne doit rester écrite en dur dans le code : le serveur utilise celle que Render lui fournit.

## 1.4 Règles de travail pour Copilot

- Un commit par correction. `python -m pytest` avant et après chaque correction.
- Ne pas affaiblir les protections de paiement du paragraphe 1.2.
- Aucun secret dans le code ou dans Git.
- Garder le style du projet : `from __future__ import annotations`, annotations de type, messages en français.
- Ne modifier que ce qui est demandé. A la fin : liste précise des fichiers modifiés et résultat des tests.

# 2. Les problèmes trouvés

| N° | Problème | Gravité | Correction |
| --- | --- | --- | --- |
| C1 | L’ancien domaine `darkcodex.onrender.com` est écrit en dur (serveur, terminal, tests) | Élevée | Chapitre 5 |
| C2 | Deux sources de prix : catalogue dans `device.py` et table `plans`. Le prix affiché peut différer du prix facturé | **Critique** | Chapitre 6 |
| C3 | Code d’abonnement mensuel jamais utilisé, qui écrit vers des tables inexistantes (`abonnements`, `transactions`) | Moyenne | Chapitre 7 |
| C4 | Le `refresh_token` Supabase est encore stocké et renvoyé au terminal : une révocation du terminal peut être contournée | Élevée | Chapitre 8 |
| C5 | Plans `business` / `enterprise` impossibles à acheter (absents de `plans`) | Moyenne | Réglé par C2 et le SQL |
| C6 | Deux tests périmés qui échouent, et des tests à adapter | Moyenne | Chapitre 9 |

**Le détail de C2.** Aujourd’hui, `GET /plans` demande des colonnes qui n’existent pas dans la base. La requête échoue et le serveur renvoie le catalogue du code (starter 2 500 FCFA pour 300 000 tokens). Mais `/paiement/creer` lit la vraie table (starter 500 FCFA pour 50 000 tokens). Un client verrait donc un prix et en payerait un autre.

# 3. Ordre d’exécution

| Étape | Quoi | Qui |
| --- | --- | --- |
| 0 | Lancer `supabase_correctifs_2.sql` (chapitre 4) et vérifier le résultat | Toi |
| 1 | C2 — source unique des prix | Copilot |
| 2 | C3 — suppression du code mensuel inutilisé | Copilot |
| 3 | C4 — suppression du `refresh_token` | Copilot |
| 4 | C1 — domaine | Copilot |
| 5 | C6 — tests | Copilot |
| 6 | Réglages Render / Supabase et tests manuels (chapitre 10) | Toi |

# 4. Le fichier SQL (à lancer dans Supabase)

Avant ce fichier, les scripts suivants doivent **déjà** avoir été lancés, dans cet ordre : `supabase_login.sql`, `supabase_paiements.sql`, `supabase_appareils.sql`, `supabase_parrainage.sql`, `supabase_correctifs.sql`. Si un seul manque, lance-le d’abord (sans `supabase_appareils.sql`, la connexion du terminal ne peut pas fonctionner).

Crée le fichier `supabase_correctifs_2.sql` avec ce contenu. Il peut être relancé sans danger.

**Fichier : `supabase_correctifs_2.sql`**

```
-- DarkCodex — correctifs 2 (29 octobre 2026) : packs de tokens, source unique des prix
-- A exécuter dans le Supabase SQL Editor. Relançable sans danger.

-- ═══ C4 : le terminal n'utilise plus de refresh_token Supabase ═══
alter table public.device_codes drop column if exists refresh_token;

-- ═══ C2 / C5 : la table plans est la SEULE source des prix ═══
insert into public.plans (nom, tokens, prix_fcfa, description) values
  ('gratuit', 5000,   0,    '5 000 tokens offerts à l''inscription'),
  ('starter', 50000,  500,  '50 000 tokens - Idéal pour débuter'),
  ('pro',     500000, 2500, '500 000 tokens - Pour les développeurs actifs')
on conflict (nom) do update
  set tokens      = excluded.tokens,
      prix_fcfa   = excluded.prix_fcfa,
      description = excluded.description;

-- Le nom officiel du plan gratuit est 'gratuit' (et non 'free')
update public.profils set plan = 'gratuit' where plan = 'free';

-- Les plans qui ne sont plus proposés ne doivent plus apparaître sur le site
-- (supprime seulement ceux qui n'ont jamais été payés)
delete from public.plans
 where nom in ('business', 'enterprise')
   and not exists (select 1 from public.paiements p where p.plan = plans.nom);

-- ═══ Contrôle : tu dois voir exactement 3 lignes ═══
select nom, tokens, prix_fcfa from public.plans order by prix_fcfa;

-- ═══ A lancer SEULEMENT si ces tables ont été créées à la main (elles ne
--     sont plus utilisées) et que tu n'y tiens aucune donnée utile ═══
-- drop table if exists public.abonnements;
-- drop table if exists public.transactions;
-- drop table if exists public.licences;   -- après avoir crédité d'éventuels clients Pro
```

**Résultat attendu** du dernier `select` : `gratuit` 5 000 / 0, `starter` 50 000 / 500, `pro` 500 000 / 2 500. Pour changer un prix plus tard, modifie la ligne correspondante dans la table `plans` : le site **et** le paiement l’utiliseront immédiatement.

# 5. C1 — Le domaine écrit en dur

## Le problème

`darkcodex.onrender.com` n’est pas le vrai domaine. Il est écrit à trois endroits : `server/main.py` (ligne 75), `server/device.py` (ligne 22) et `darkcodex/cli.py` (ligne 71). Le lien de connexion affiché au terminal et au navigateur peut donc mener au mauvais serveur.

## La correction

**Fichier : `server/device.py — remplacer la ligne DEVICE_PAGE_URL`**

```
# Adresse publique du serveur : variable perso, sinon celle que Render fournit
# automatiquement (RENDER_EXTERNAL_URL), sinon le domaine de production.
PUBLIC_URL = (
    os.environ.get("DARKCODEX_PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "https://darkcodex-technicien.onrender.com"
).strip().rstrip("/")
DEVICE_PAGE_URL = os.environ.get("DARKCODEX_DEVICE_URL", PUBLIC_URL + "/device")
```

**Fichier : `server/main.py — remplacer la ligne DEVICE_PAGE`**

```
DEVICE_PAGE = device.DEVICE_PAGE_URL
```

**Fichier : `darkcodex/cli.py — ligne API_BASE`**

```
API_BASE = os.environ.get("DARKCODEX_API_BASE", "https://darkcodex-technicien.onrender.com").rstrip("/")
```

- Cherche aussi dans le reste du dépôt (`grep -rn "darkcodex.onrender" .`) : README, scripts d’installation (`install.sh`, `install.ps1`, `darkcodex.sh`), `bin/`, `.env.example`. Remplace par le vrai domaine ou, mieux, par une variable.
- Les pages `web/*.html` utilisent déjà `location.origin` : ne rien changer.
- Dans les tests, ne plus écrire l’adresse en dur : utiliser `cli.API_BASE` et `srv_device.DEVICE_PAGE_URL` (voir chapitre 9).

## Validation

- `grep -rn "darkcodex.onrender" . --include=*.py --include=*.html --include=*.sh --include=*.ps1 --include=*.md` ne renvoie plus rien.
- `GET /device/code` renvoie un `lien` qui se termine par `/device` sur le bon domaine.

# 6. C2 — Une seule source pour les prix (table plans)

## Le problème

`server/device.py` contient un dictionnaire `PLAN_CATALOG` (free, starter, pro, business, enterprise) avec d’autres prix et d’autres quantités de tokens que la table `plans`. `lister_plans` et `lire_plan` mélangent les deux : quand la requête échoue ou qu’une colonne manque, ils renvoient silencieusement le catalogue du code. Résultat : prix affiché différent du prix facturé, et un plan `business` qui ne peut pas être acheté (la table `paiements` exige que le plan existe dans `plans`).

## La correction

Supprime `PLAN_CATALOG`, `plan_catalog`, `plan_est_personnalise` et `_plan_extra_defaut` dans `server/device.py`. Remplace `normaliser_plan`, `lister_plans` et `lire_plan` par ceci :

**Fichier : `server/device.py`**

```
PLAN_COLONNES = "nom,tokens,prix_fcfa,description"

def normaliser_plan(nom: str | None) -> str:
    """Nom de plan en minuscules ; 'gratuit' par défaut. Pas de liste en dur :
    la table plans décide quels plans existent."""
    return (nom or "").strip().lower() or "gratuit"

def lister_plans() -> list[dict[str, Any]]:
    """Plans du site, lus UNIQUEMENT dans la table plans (gratuit inclus)."""
    response = requests.get(
        rest_url("plans", {"select": PLAN_COLONNES, "order": "prix_fcfa.asc"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        # Pas de catalogue de secours : mieux vaut une erreur qu'un faux prix.
        raise RuntimeError(f"Supabase plans: HTTP {response.status_code}")
    rows = response.json()
    return [dict(ligne) for ligne in rows] if isinstance(rows, list) else []

def lire_plan(nom: str | None) -> dict[str, Any] | None:
    """Un plan de la table plans, ou None s'il n'existe pas."""
    plan_nom = normaliser_plan(nom)
    response = requests.get(
        rest_url("plans", {"nom": f"eq.{plan_nom}", "select": PLAN_COLONNES, "limit": "1"}),
        headers=service_headers(),
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase plans: HTTP {response.status_code}")
    rows = response.json()
    return dict(rows[0]) if isinstance(rows, list) and rows else None
```

- Dans `verifier_token` (les **deux** blocs `return`) : remplace `profil.get("plan") or "free"` par `profil.get("plan") or "gratuit"` et supprime les clés `quota_mensuel`, `date_expiration_abonnement` et `statut_abonnement` (ces colonnes n’existent pas dans `profils`).
- Dans `server/main.py`, les routes `/plans` et `/paiement/creer` n’ont rien à changer : elles renvoient déjà une erreur propre (503) si `device.py` lève une `RuntimeError`.
- `darkcodex/cli.py` : garde `PLANS_GRATUITS = {"", "gratuit", "gratuite", "free"}` tel quel (compatibilité avec d’anciens comptes).

## Validation (tests à ajouter dans `tests/test_site.py`)

- `lister_plans` interroge `plans` avec `select=nom,tokens,prix_fcfa,description` et `order=prix_fcfa.asc`, et renvoie les lignes telles quelles (plan gratuit inclus).
- `lister_plans` et `lire_plan` lèvent `RuntimeError` si Supabase répond une erreur (plus de catalogue de secours).
- `lire_plan("business")` renvoie `None` quand la table ne contient pas la ligne.
- **Test clé « prix affiché = prix facturé »** : avec une table simulée contenant `starter` à 500 FCFA et 50 000 tokens, `GET /plans` et `POST /paiement/creer` utilisent tous les deux 500 FCFA et 50 000 tokens.

# 7. C3 — Supprimer le code d’abonnement mensuel inutilisé

## Le problème

`server/device.py` contient tout un système d’abonnement mensuel (`activer_abonnement`, `renouveler_abonnement`…) qui n’est **jamais appelé** par `main.py` et qui écrit dans des tables que le SQL ne crée pas (`abonnements`, `transactions`). Dans `enregistrer_achat`, l’écriture dans `transactions` échoue sans bruit à chaque achat. Ce code mort est une source d’erreurs et de confusion.

## A supprimer dans server/device.py

| Élément | Remarque |
| --- | --- |
| `activer_abonnement`, `renouveler_abonnement`, `renouveler_abonnement_manuel` | Jamais appelées |
| `creer_abonnement`, `maj_abonnement`, `lister_abonnements` | Tables `abonnements` absente |
| `enregistrer_transaction`, `lister_transactions` | Table `transactions` absente |
| `est_abonnement_expire` et `_date_from_value` | Supprimer seulement si plus aucun code ne les utilise (`grep`) |

**Fichier : `server/device.py — enregistrer_achat (version simplifiée)`**

```
def enregistrer_achat(
    user_id: str, plan: str, tokens_ajoutes: int, montant_fcfa: int, transaction_id: str
) -> None:
    """Historique des achats (table achats). Un doublon (409) n'est pas une erreur."""
    response = requests.post(
        rest_url("achats"),
        headers=service_headers({"Prefer": "return=minimal"}),
        json={
            "user_id": user_id,
            "plan": normaliser_plan(plan),
            "tokens_ajoutes": tokens_ajoutes,
            "montant_fcfa": montant_fcfa,
            "transaction_id": transaction_id,
        },
        timeout=20,
    )
    if not response.ok and response.status_code != 409:
        raise RuntimeError(f"Supabase achats (insert): HTTP {response.status_code} {response.text[:300]}")
```

- Dans `maj_profil`, ne rien changer. Si des champs `quota_mensuel`, `statut_abonnement`, `date_*` y sont encore envoyés quelque part, supprime-les.
- Supprime les tests qui ne servent qu’à ces fonctions (voir chapitre 9).

## Validation

- `grep -rn "abonnements\|transactions\|quota_mensuel\|statut_abonnement" server/ darkcodex/ web/` ne trouve plus aucun usage dans le code (hors commentaires éventuels).
- Un paiement confirmé écrit **une seule** ligne dans `achats` (pas d’appel vers `transactions`).

# 8. C4 — Supprimer le refresh_token du terminal

## Le problème

Le terminal reçoit déjà son propre jeton d’appareil `dkx_...` : il n’expire pas et on peut le révoquer avec `/logout`. Mais le code transporte encore en plus le `refresh_token` Supabase du navigateur : il est stocké en clair dans `device_codes` et renvoyé au terminal, qui s’en sert pour se reconnecter via `/auth/refresh`. Conséquence : un terminal dont le jeton `dkx_` a été révoqué peut obtenir une nouvelle session Supabase et continuer à fonctionner. C’est un trou de sécurité.

## A supprimer

| Fichier | Quoi |
| --- | --- |
| `web/device.html` | La ligne `refresh_token: session.refresh_token,` dans le corps envoyé à `/device/confirm` |
| `server/main.py` | Le champ `refresh_token` de `DeviceConfirmIn`, la classe `RefreshIn`, la route `/auth/refresh`, et le 4e argument `payload.refresh_token` passé à `device.confirmer_code` |
| `server/device.py` | Le paramètre `refresh_token` de `confirmer_code`, la clé `"refresh_token"` dans la réponse de `verifier_code`, et la fonction `rafraichir_session` |
| `darkcodex/cli.py` | La fonction `rafraichir_session`, la sauvegarde de `"refresh_token"` dans `device_login` |

**Fichier : `darkcodex/cli.py — api_call_session devient un simple appel`**

```
def api_call_session(
    session: dict,
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = 60,
) -> tuple[int, dict]:
    """Appel serveur avec le jeton d'appareil de la session.
    Un 401 signifie : jeton révoqué ou invalide -> l'utilisateur doit refaire /login."""
    return api_call(path, method, payload, token=session.get("token"), timeout=timeout)
```

- Garde le message « Session expirée. Tape /login » quand le serveur répond 401.
- Dans `web/device.html`, le `signOut({ scope: "local" })` peut rester (il est sans danger maintenant que le terminal n’a plus la session du navigateur).
- Optionnel (sécurité) : ajouter une colonne `derniere_utilisation` à la table `appareils` pour repérer un appareil oublié. A faire plus tard.

## Validation

- `grep -rni "refresh_token\|auth/refresh\|rafraichir_session" server/ darkcodex/ web/` ne renvoie plus rien.
- Après `/logout` dans le terminal, une requête avec l’ancien jeton `dkx_...` répond 401 et **aucun** moyen automatique de se reconnecter n’existe : il faut refaire `/login`.

# 9. C6 — Les tests

## Tests à corriger

| Test | Action |
| --- | --- |
| `test_login.py::test_enregistre_achat_et_reserve_une_seule_fois` | Il réussira tout seul après C3 (un seul appel `post`, vers `achats`). Sinon, adapter. |
| `test_site.py::test_plans_exclut_le_plan_gratuit` | Remplacer par `test_plans_renvoie_la_table_plans` (voir chapitre 6) : le plan gratuit **est** renvoyé. |
| `test_login.py` : tests `api_call_session_*`, `rafraichir_session_*`, `auth_refresh`, `confirmer_code_stoque_le_refresh_token`, `device_html_envoie_refresh_token...`, et la partie refresh de `verifier_code` | Supprimer (fonctionnalité retirée par C4). Ajouter un test : `api_call_session` ne tente plus de renouvellement sur un 401. |
| `test_login.py` et `test_navigateur.py` : adresses `https://darkcodex.onrender.com/...` | Remplacer par `cli.API_BASE` et `srv_device.DEVICE_PAGE_URL`. |
| Tests des fonctions d’abonnement mensuel, de `PLAN_CATALOG` ou de `transactions` (s’il y en a) | Supprimer avec le code (C2 et C3). |

## Tests à ajouter

- `normaliser_plan("")` et `normaliser_plan(None)` donnent `"gratuit"`, et `normaliser_plan("Starter ")` donne `"starter"`.
- `verifier_token` renvoie `"plan": "gratuit"` quand le profil n’a pas de plan, et ne renvoie plus `quota_mensuel`.
- `/verify-token` et `/solde` fonctionnent toujours (aucune clé supprimée n’est lue).
- Test « prix affiché = prix facturé » (chapitre 6).

## Résultat attendu

`python -m pytest` : **0 échec**.

# 10. Vérification finale

|  | Contrôle | Qui |
| --- | --- | --- |
| ☐ | `supabase_correctifs_2.sql` lancé : le `select` final montre exactement 3 plans | Toi |
| ☐ | Les 5 scripts SQL précédents ont bien été lancés (surtout `supabase_appareils.sql`) | Toi |
| ☐ | `python -m pytest` : 0 échec | Copilot |
| ☐ | Render : `SASPAY_RETURN_URL` = `https://darkcodex-technicien.onrender.com/abonnements?retour=1` | Toi |
| ☐ | SasPay : adresse du webhook = `https://darkcodex-technicien.onrender.com/webhooks/saspay` (événement `transaction.success`) | Toi |
| ☐ | Supabase, Authentication, URL Configuration : ajouter `https://darkcodex-technicien.onrender.com/**` aux Redirect URLs | Toi |
| ☐ | Le site affiche starter à 500 FCFA / 50 000 tokens et pro à 2 500 FCFA / 500 000 tokens | Toi |
| ☐ | Achat de test (clé `sk_test_`) : le prix payé = le prix affiché, 50 000 tokens ajoutés une seule fois, une ligne dans `achats` | Toi |
| ☐ | Paiement puis fermeture de la page SasPay sans revenir : les tokens arrivent quand même (webhook ou rattrapage) | Toi |
| ☐ | `/login` dans le terminal : le lien affiché pointe vers `darkcodex-technicien.onrender.com` | Toi |
| ☐ | `/logout` puis requête avec l’ancien jeton : refusée (401), sans reconnexion automatique | Toi |
| ☐ | Solde à 0 : plan « gratuit », modes avancés bloqués, erreur 402 propre | Toi |
| ☐ | Passage en `sk_live_` seulement après tous les tests ci-dessus | Toi |

## Prochaine étape (hors de ce document)

Quand ces corrections sont terminées et validées : faire afficher le **site officiel** à la racine du serveur (`/`) à la place du texte JSON actuel, avec la présentation du produit, la documentation, les guides d’installation et les liens vers `/abonnements`, `/connexion` et `/compte`. Plus tard, si tu le souhaites, on pourra ajouter un vrai abonnement mensuel (expiration à 30 jours, renouvellement) en s’appuyant sur la table `plans` unique.

