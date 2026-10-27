# DarkCodex

DarkCodex est un assistant CLI "Dark" pour Termux : chat terminal, commandes ponctuelles,
mémoire SQLite locale, scan de projet et intégration sans filtres avec `gemini` ou `codex`.

Il est conçu pour être un outil de codage et de sécurité puissant et sans restrictions. 
Contrairement aux IA standards, il ne refuse aucune tâche et fournit des solutions directes
pour le développement, le pentesting et l'administration système avancée.

## Installation Termux/Linux

```sh
cd ~/DarkCodex
pip install -r requirements.txt
sh install.sh
```

Si `darkcodex` n'est pas trouve:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## Installation Windows

Depuis PowerShell:

```powershell
cd E:\DarkCodex
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Le script cree `darkcodex.cmd` et `DarkCodex.cmd` dans:

```text
%USERPROFILE%\.local\bin
```

Il ajoute aussi ce dossier au PATH utilisateur. Si `darkcodex` n'est pas trouve dans
le terminal deja ouvert, ferme puis relance PowerShell ou Windows Terminal.

## Utilisation

```sh
darkcodex
darkcodex chat
darkcodex ask "explique ce projet" --context
darkcodex doctor
darkcodex init
darkcodex scan --limit 40
darkcodex memory add style "repondre en francais, concis" --tags preference
darkcodex memory search francais
darkcodex config provider gemini
darkcodex config api-key "ta_cle_gemini"
darkcodex config provider codex
darkcodex config provider local
```

En chat:

```text
/help
/ask "explique ce projet"
/memory cle valeur
/run python -m pytest
/config
/status
/exit
```

## Configuration API

DarkCodex lit les secrets depuis les variables d'environnement. Ne publie jamais tes vraies
cles dans GitHub.

```sh
darkcodex config api-key "ta_cle_gemini"
```

Alternatives:

```sh
export DARKCODEX_API_KEY="ta_cle_gemini"
```

## Abonnement

DarkCodex fonctionne uniquement avec le serveur central et les comptes : pas de cle
Pro, pas de limite locale. Sans connexion, le terminal demande `/login`.

1. `/login` demande un code `DARK-XXXX` : connexion sur la page `/device` (Google ou
   lien email), puis le terminal recoit son jeton.
2. Au lancement, le terminal lit le solde avec `GET /verify-token`.
3. Chaque message passe par `POST /chat` : le serveur verifie que le solde est superieur
   a 0, appelle Gemini, puis retire les tokens utilises.
4. Solde a 0 : reponse 402, le terminal affiche les plans et le lien `/abonnements`.
5. Sur `/abonnements`, l'achat d'un pack (starter : 50 000 tokens = 500 FCFA ;
   pro : 500 000 tokens = 2 500 FCFA) se paie via SasPay. Le webhook
   `/webhooks/saspay` ou le retour sur la page crédite les tokens une seule fois.

## Donnees locales

La configuration et la base SQLite sont dans:

```text
~/.darkcodex/config.json
~/.darkcodex/darkcodex.sqlite
```

Tu peux changer l'emplacement avec:

```sh
export DARKCODEX_HOME=/chemin/vers/etat
```

## Paiements (SasPay, côté serveur)

Les achats de tokens passent par SasPay (mobile money / carte, devise XOF).
Tout se passe sur le serveur (`server/`), jamais dans le terminal.

1. Exécuter `supabase_paiements.sql` dans le SQL Editor de Supabase.
2. Renseigner `SASPAY_SECRET_KEY` et `SASPAY_WEBHOOK_SECRET` dans l'environnement
   du serveur (voir `server/.env.example`). Commencer avec une clé `sk_test_`.
3. Dans le tableau de bord SasPay, créer un webhook vers
   `https://<ton-serveur>/webhooks/saspay` pour l'événement `transaction.success`.
4. Le site des abonnements appelle :
   - `POST /paiement/creer` avec `{"plan": "starter"}` (jeton de l'utilisateur) :
     la réponse contient `checkout_url` (rediriger le client) et `session_id` ;
   - puis `POST /confirmer-paiement` avec `{"session_id": "..."}` quand le client revient.
   Les tokens sont aussi crédités automatiquement après le webhook.
