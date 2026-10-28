DarkCodex

Consignes pour Codex : quatre nouvelles fonctionnalités

Historique et annulation, coût en tokens, dictée vocale, parrainage — avec le code à copier et testé

# 1. Contexte

Ce document ajoute quatre fonctionnalités au terminal DarkCodex et au serveur. Il suppose que le document précédent (« Connexion, abonnements et mises à jour », parties P01 à P10) est déjà appliqué : le code ci-dessous s'appuie sur darkcodex/updater.py, darkcodex/navigateur.py et sur le système de comptes et de jetons.

Les quatre fonctionnalités

Un bug corrigé au passage

Dans ai_answer() (utilisée par /lire, /corriger, /resume...), le code comparait le résultat à 200 alors que server_answer() renvoie 0 en cas de succès. Conséquence : le solde de tokens n'était pas mis à jour et la ligne « Tokens utilisés » n'apparaissait pas après ces commandes. La partie Q2 le corrige.

Exemples de ce que verra l'utilisateur

Exemples (la mise en forme exacte peut varier)

> /historique

1. 27/10 14:32  modifié  src/app.py

> /annuler

Annuler la dernière modification (restaurer : src/app.py) ? (o/n) o

✅ Restauré : src/app.py (version d'avant la modification du 27/10 14:32)

> /limite 20000

Limite de session : 20 000 tokens.

> /dicter

🎤 Parle maintenant...

J'ai compris : « explique le fichier app point py »

Envoyer ? [O = oui, n = annuler, m = modifier]

> /parrainer

Ton code : DK-AB23CD   (chaque filleul : +2 000 tokens pour lui ET pour toi)

# 2. Règles à respecter (obligatoires)

Fais une partie à la fois, dans l'ordre Q1 à Q6, avec un commit par partie (message : « Q2 : coût en tokens »).

Ne modifie que les fichiers listés dans la partie en cours. Aucun renommage, aucune nouvelle dépendance Python.

Copie le code fourni tel quel. Si une ligne à remplacer ne correspond pas exactement au dépôt, arrête-toi et explique la différence au lieu d'improviser.

Le dépôt est public : aucune vraie clé dans le code, les tests ou les logs.

Après chaque partie : python -m unittest discover -s tests. Tous les tests doivent passer avant de continuer.

Ne change pas le comportement de /chat, /solde, des paiements ni de la connexion.

Pour les tests du serveur : pip install fastapi uvicorn httpx requests (sans httpx, test_parrainage.py est ignoré, mais ne doit pas être supprimé).

À la fin, envoie un rapport : fichiers modifiés par partie et résultat des tests.

# 3. Vue d'ensemble des parties

## Q1 — Historique : /historique, /annuler, /diff

Objectif

Avant chaque modification faite par DarkCodex, l'ancienne version du fichier est copiée dans <projet>/.darkcodex/historique/ (un fichier .gitignore contenant * y est créé : l'historique ne part jamais dans Git). Seuls les fichiers du projet de moins de 2 Mo sont sauvegardés, et on garde les 200 dernières modifications. La sauvegarde ne bloque jamais l'écriture : si elle échoue, la modification continue.

Fichiers

darkcodex/historique.py (nouveau), darkcodex/agent_fichiers.py, darkcodex/cli.py, tests/test_historique.py (nouveau)

Consignes

Crée darkcodex/historique.py avec le code ci-dessous.

Dans darkcodex/cli.py, remplace la ligne d'import from . import navigateur, updater par from . import historique, navigateur, updater (si ta ligne est différente, ajoute seulement historique dedans).

Branche l'historique aux trois endroits où DarkCodex écrit un fichier (voir le second bloc de code).

Crée tests/test_historique.py. Les tests de l'intégration dans le chat sont en Q5.

darkcodex/historique.py

"""Historique des modifications de fichiers : /historique, /annuler, /diff.

Avant chaque modification faite par DarkCodex, l'ancienne version du fichier est

copiée dans <projet>/.darkcodex/historique/. /annuler restaure la dernière version.

/annuler sauvegarde aussi l'état courant : deux /annuler de suite = rétablir.

"""

from __future__ import annotations

import difflib

import json

import os

from datetime import datetime, timezone

from pathlib import Path

from .fsutil import atomic_write_text

DOSSIER = Path(".darkcodex") / "historique"

INDEX = "index.json"

MAX_ENTREES = 200

TAILLE_MAX = 2_000_000  # les fichiers plus gros ne sont pas sauvegardés

LIGNES_DIFF_MAX = 200

def dossier_historique(racine: Path) -> Path:

return Path(racine) / DOSSIER

def chemin_relatif(racine: Path, chemin: Path) -> str | None:

"""Chemin relatif au projet, ou None si le fichier est hors du projet."""

try:

return Path(chemin).resolve().relative_to(Path(racine).resolve()).as_posix()

except (ValueError, OSError):

return None

def _lire_index(dossier: Path) -> list[dict]:

try:

data = json.loads((dossier / INDEX).read_text(encoding="utf-8"))

return data if isinstance(data, list) else []

except (OSError, ValueError):

return []

def _ecrire_index(dossier: Path, entrees: list[dict]) -> None:

atomic_write_text(dossier / INDEX, json.dumps(entrees, ensure_ascii=False, indent=1) + "\n")

def _ecrire_octets(destination: Path, contenu: bytes) -> None:

tmp = destination.with_name(destination.name + ".tmp")

tmp.write_bytes(contenu)

os.replace(tmp, destination)

def enregistrer(racine: Path, chemin: Path) -> dict | None:

"""À appeler AVANT d'écrire `chemin`. Ne lève jamais d'exception (ne bloque pas l'écriture)."""

try:

racine, chemin = Path(racine), Path(chemin)

rel = chemin_relatif(racine, chemin)

if rel is None or rel.startswith(".darkcodex/"):

return None

existait = chemin.is_file()

contenu = b""

if existait:

if chemin.stat().st_size > TAILLE_MAX:

return None

contenu = chemin.read_bytes()

dossier = dossier_historique(racine)

dossier.mkdir(parents=True, exist_ok=True)

garde = dossier / ".gitignore"

if not garde.exists():

garde.write_text("*\n", encoding="utf-8")  # l'historique ne part jamais dans Git

entree_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")

sauvegarde = None

if existait:

sauvegarde = f"{entree_id}.bak"

_ecrire_octets(dossier / sauvegarde, contenu)

entrees = _lire_index(dossier)

entree = {

"id": entree_id,

"chemin": rel,

"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),

"existait": existait,

"sauvegarde": sauvegarde,

"annule": False,

}

entrees.append(entree)

while len(entrees) > MAX_ENTREES:  # on garde les 200 dernières

ancienne = entrees.pop(0)

if ancienne.get("sauvegarde"):

try:

(dossier / ancienne["sauvegarde"]).unlink()

except OSError:

pass

_ecrire_index(dossier, entrees)

return entree

except OSError:

return None

def lister(racine: Path, limite: int = 10, chemin: str | None = None) -> list[dict]:

"""Entrées les plus récentes d'abord (filtre facultatif sur un chemin relatif)."""

entrees = _lire_index(dossier_historique(racine))

if chemin:

entrees = [e for e in entrees if e.get("chemin") == chemin]

return list(reversed(entrees))[:limite]

def prochaine_annulation(racine: Path) -> dict | None:

for entree in lister(racine, limite=MAX_ENTREES):

if not entree.get("annule"):

return entree

return None

def annuler(racine: Path) -> tuple[bool, str]:

"""Restaure la version enregistrée avant la dernière modification non annulée."""

racine = Path(racine)

entree = prochaine_annulation(racine)

if entree is None:

return False, "Rien à annuler."

cible = racine / entree["chemin"]

if chemin_relatif(racine, cible) is None:

return False, "Chemin invalide dans l'historique."

dossier = dossier_historique(racine)

sauvegarde = dossier / entree["sauvegarde"] if entree.get("sauvegarde") else None

if sauvegarde is not None and not sauvegarde.exists():

return False, "La sauvegarde de cette modification a disparu."

enregistrer(racine, cible)  # état actuel conservé : /annuler peut lui-même être annulé

try:

if sauvegarde is None:

if cible.exists():

cible.unlink()  # le fichier avait été créé par DarkCodex

else:

cible.parent.mkdir(parents=True, exist_ok=True)

_ecrire_octets(cible, sauvegarde.read_bytes())

except OSError as exc:

return False, f"Restauration impossible : {exc}"

entrees = _lire_index(dossier)

for e in entrees:

if e.get("id") == entree["id"]:

e["annule"] = True

_ecrire_index(dossier, entrees)

return True, f"Restauré : {entree['chemin']} (version d'avant la modification du {formater_date(entree['date'])})"

def formater_date(iso: str) -> str:

try:

return datetime.fromisoformat(iso).astimezone().strftime("%d/%m %H:%M")

except ValueError:

return iso

def formater(entrees: list[dict]) -> str:

if not entrees:

return "Aucune modification enregistrée."

lignes = []

for i, e in enumerate(entrees, 1):

etat = "annulée" if e.get("annule") else ("créé" if not e.get("existait") else "modifié")

lignes.append(f"{i:>2}. {formater_date(e['date'])}  {etat:<8} {e['chemin']}")

return "\n".join(lignes)

def diff_derniere(racine: Path, chemin: str | None = None) -> tuple[bool, str]:

"""Différences entre la version sauvegardée (avant) et le fichier actuel (après)."""

racine = Path(racine)

rel = chemin_relatif(racine, racine / chemin) if chemin else None

if chemin and rel is None:

return False, "Ce fichier est hors du projet."

entrees = lister(racine, limite=1, chemin=rel)

if not entrees:

return False, "Aucune modification enregistrée" + (f" pour {rel}." if rel else ".")

entree = entrees[0]

dossier = dossier_historique(racine)

try:

avant = (dossier / entree["sauvegarde"]).read_text(encoding="utf-8", errors="replace") if entree.get("sauvegarde") else ""

except OSError:

return False, "La sauvegarde de cette modification a disparu."

try:

apres = (racine / entree["chemin"]).read_text(encoding="utf-8", errors="replace")

except OSError:

apres = ""

lignes = list(difflib.unified_diff(

avant.splitlines(), apres.splitlines(), fromfile=f"avant/{entree['chemin']}", tofile=f"maintenant/{entree['chemin']}", lineterm="",

))

if not lignes:

return True, "Aucune différence avec la version sauvegardée."

if len(lignes) > LIGNES_DIFF_MAX:

lignes = lignes[:LIGNES_DIFF_MAX] + [f"... ({len(lignes) - LIGNES_DIFF_MAX} lignes de plus)"]

return True, "\n".join(lignes)

def colorier_diff(texte: str) -> str:

sortie = []

for ligne in texte.splitlines():

if ligne.startswith("+") and not ligne.startswith("+++"):

sortie.append(f"\033[32m{ligne}\033[0m")

elif ligne.startswith("-") and not ligne.startswith("---"):

sortie.append(f"\033[31m{ligne}\033[0m")

else:

sortie.append(ligne)

return "\n".join(sortie)

Les trois branchements

# darkcodex/agent_fichiers.py — en haut, après « from pathlib import Path » :

from . import historique

# darkcodex/agent_fichiers.py — dans appliquer_fichier(), juste AVANT « write_generated_file(cible, nettoye) » :

historique.enregistrer(getattr(ctx, "cwd", None) or cible.parent, cible)

write_generated_file(cible, nettoye)

# darkcodex/cli.py — dans handle_file_agent_command(), commande /generer :

historique.enregistrer(cwd, path)

agent_fichiers.write_generated_file(path, output)

# darkcodex/cli.py — dans handle_file_agent_command(), commande /corriger :

backup = agent_fichiers.backup_file(path)

historique.enregistrer(cwd, path)

agent_fichiers.write_generated_file(path, output)

tests/test_historique.py

import tempfile

import unittest

from pathlib import Path

from unittest import mock

from darkcodex import historique

class HistoriqueTests(unittest.TestCase):

def setUp(self):

self.tmp = tempfile.TemporaryDirectory()

self.racine = Path(self.tmp.name)

self.fichier = self.racine / "app.py"

def tearDown(self):

self.tmp.cleanup()

def test_annuler_restaure_l_ancienne_version(self):

self.fichier.write_text("v1")

historique.enregistrer(self.racine, self.fichier)

self.fichier.write_text("v2")

ok, message = historique.annuler(self.racine)

self.assertTrue(ok, message)

self.assertEqual(self.fichier.read_text(), "v1")

def test_annuler_deux_fois_retablit(self):

self.fichier.write_text("v1")

historique.enregistrer(self.racine, self.fichier)

self.fichier.write_text("v2")

historique.annuler(self.racine)

self.assertEqual(self.fichier.read_text(), "v1")

historique.annuler(self.racine)

self.assertEqual(self.fichier.read_text(), "v2")

def test_fichier_cree_est_supprime_par_annuler(self):

historique.enregistrer(self.racine, self.fichier)  # le fichier n'existe pas encore

self.fichier.write_text("nouveau")

ok, _ = historique.annuler(self.racine)

self.assertTrue(ok)

self.assertFalse(self.fichier.exists())

historique.annuler(self.racine)  # rétablir

self.assertEqual(self.fichier.read_text(), "nouveau")

def test_rien_a_annuler(self):

self.assertEqual(historique.annuler(self.racine), (False, "Rien à annuler."))

def test_fichier_hors_du_projet_ignore(self):

with tempfile.TemporaryDirectory() as autre:

dehors = Path(autre) / "x.txt"

dehors.write_text("a")

self.assertIsNone(historique.enregistrer(self.racine, dehors))

def test_l_historique_n_est_jamais_versionne(self):

self.fichier.write_text("v1")

historique.enregistrer(self.racine, self.fichier)

self.assertEqual((historique.dossier_historique(self.racine) / ".gitignore").read_text(), "*\n")

def test_seules_les_dernieres_entrees_sont_gardees(self):

self.fichier.write_text("v1")

with mock.patch.object(historique, "MAX_ENTREES", 3):

for i in range(5):

self.fichier.write_text(f"v{i}")

historique.enregistrer(self.racine, self.fichier)

self.assertEqual(len(historique.lister(self.racine, limite=50)), 3)

sauvegardes = list(historique.dossier_historique(self.racine).glob("*.bak"))

self.assertEqual(len(sauvegardes), 3)

def test_diff_montre_avant_et_apres(self):

self.fichier.write_text("a\nb\n")

historique.enregistrer(self.racine, self.fichier)

self.fichier.write_text("a\nc\n")

ok, texte = historique.diff_derniere(self.racine)

self.assertTrue(ok)

self.assertIn("-b", texte)

self.assertIn("+c", texte)

self.assertEqual(historique.diff_derniere(self.racine, "../secret.txt")[0], False)

def test_liste_formatee(self):

self.assertEqual(historique.formater([]), "Aucune modification enregistrée.")

self.fichier.write_text("v1")

historique.enregistrer(self.racine, self.fichier)

self.assertIn("app.py", historique.formater(historique.lister(self.racine)))

if __name__ == "__main__":

unittest.main()

Vérification

python -m unittest discover -s tests : tout doit passer.

## Q2 — Coût en tokens et correction du bug du solde

Objectif

Un module cout.py estime le nombre de tokens d'une demande (environ 4 caractères par token : approximatif, le décompte réel et facturé reste celui du serveur), garde le total de la session, applique une limite fixée par /limite, et demande confirmation avant une grosse demande (seuil : 5 000 tokens). La confirmation n'est posée que dans un vrai terminal, jamais dans un script. Pour les valeurs par défaut, on peut utiliser les variables DARKCODEX_MAX_TOKENS et DARKCODEX_COST_CONFIRM.

Fichiers

darkcodex/cout.py (nouveau), darkcodex/cli.py, tests/test_cout.py (nouveau)

Consignes

Crée darkcodex/cout.py avec le code ci-dessous.

Dans darkcodex/cli.py, change la ligne d'import en from . import cout, historique, navigateur, updater.

Dans ai_answer(), applique les deux modifications indiquées. La première corrige le bug (code == 200 devient code == 0).

Crée tests/test_cout.py.

darkcodex/cout.py

"""Estimation et suivi du coût en tokens : /cout, /limite, confirmation avant les grosses demandes.

L'estimation est approximative (environ 4 caractères par token). Le décompte réel,

facturé, est celui du serveur et s'affiche après chaque réponse.

"""

from __future__ import annotations

import os

import sys

from typing import Callable

SEUIL_CONFIRMATION_DEFAUT = 5000

def estimer_tokens(texte: str) -> int:

return max(1, (len(texte or "") + 3) // 4)

def _entier_env(nom: str) -> int | None:

try:

valeur = int(os.environ.get(nom, "").strip())

except ValueError:

return None

return valeur if valeur > 0 else None

class SuiviCout:

def __init__(self, limite: int | None = None, seuil: int = SEUIL_CONFIRMATION_DEFAUT):

self.utilises = 0      # mesurés par le serveur

self.estimes = 0       # estimés (mode local, sans compte)

self.limite = limite

self.seuil = seuil

@classmethod

def depuis_environnement(cls) -> "SuiviCout":

return cls(_entier_env("DARKCODEX_MAX_TOKENS"), _entier_env("DARKCODEX_COST_CONFIRM") or SEUIL_CONFIRMATION_DEFAUT)

@property

def total(self) -> int:

return self.utilises + self.estimes

def ajouter(self, utilises: object) -> None:

try:

self.utilises += max(0, int(utilises))  # type: ignore[arg-type]

except (TypeError, ValueError):

pass

def ajouter_estimation(self, prompt: str, reponse: str) -> None:

self.estimes += estimer_tokens(prompt) + estimer_tokens(reponse)

def definir_limite(self, valeur: str) -> tuple[bool, str]:

texte = (valeur or "").strip().lower()

if texte in {"off", "non", "0", "aucune"}:

self.limite = None

return True, "Limite de session supprimée."

try:

nombre = int(texte.replace(" ", "").replace("_", ""))

except ValueError:

return False, "Usage : /limite 20000   ou   /limite off"

if nombre <= 0:

return False, "La limite doit être un nombre positif."

self.limite = nombre

return True, f"Limite de session : {nombre:,} tokens.".replace(",", " ")

def verifier(

self,

tokens_estimes: int,

confirmer: bool = True,

interactif: bool | None = None,

demander: Callable[[str], str] | None = None,

) -> tuple[bool, str]:

"""(True, "") si la demande peut partir ; sinon (False, message à afficher)."""

if self.limite is not None and self.total + tokens_estimes > self.limite:

return False, (

f"⛔ Limite de session atteinte : {self.total} utilisés + ~{tokens_estimes} estimés > {self.limite}. "

"Augmente-la avec /limite NOMBRE ou supprime-la avec /limite off."

)

if confirmer and tokens_estimes >= self.seuil:

if interactif is None:

interactif = sys.stdin.isatty() and sys.stdout.isatty()

if interactif:

try:

reponse = (demander or input)(f"Cette demande va consommer environ {tokens_estimes} tokens (estimation). Continuer ? [O/n] ")

except (EOFError, KeyboardInterrupt):

reponse = "n"

if reponse.strip().lower() in {"n", "non", "no"}:

return False, "Demande annulée."

return True, ""

SESSION = SuiviCout.depuis_environnement()

darkcodex/cli.py : fonction ai_answer()

# Dans ai_answer(), partie « session connectée » :

if session:

ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(prompt))

if not ok_cout:

return 1, message_cout

code, output, data = server_answer(session, prompt)

if code == 0:  # server_answer renvoie 0 en cas de succès (et non 200)

cout.SESSION.ajouter(data.get("tokens_utilises"))

session["tokens_solde"] = data.get("solde_restant", session.get("tokens_solde"))

save_auth(session)

show_token_line(data.get("tokens_utilises"), data.get("solde_restant"))

return 0, output

# ... (les cas 401 et 402 ne changent pas)

# Dans ai_answer(), partie « mode local » (remplace la fin de la fonction) :

ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(prompt))

if not ok_cout:

return 1, message_cout

allowed, limit_message = licence.consume_request()

if not allowed:

return 1, limit_message

code, output = run_provider(provider, prompt, config, cwd)

if code == 0:

cout.SESSION.ajouter_estimation(prompt, output)

return code, output

tests/test_cout.py

import unittest

from darkcodex import cout

class CoutTests(unittest.TestCase):

def test_estimation(self):

self.assertEqual(cout.estimer_tokens(""), 1)

self.assertEqual(cout.estimer_tokens("a" * 400), 100)

def test_limite(self):

suivi = cout.SuiviCout()

self.assertEqual(suivi.definir_limite("20000")[0], True)

self.assertEqual(suivi.limite, 20000)

self.assertFalse(suivi.definir_limite("abc")[0])

self.assertFalse(suivi.definir_limite("-5")[0])

suivi.definir_limite("off")

self.assertIsNone(suivi.limite)

def test_limite_bloque_la_demande(self):

suivi = cout.SuiviCout(limite=1000)

suivi.ajouter(900)

ok, message = suivi.verifier(200)

self.assertFalse(ok)

self.assertIn("Limite de session", message)

self.assertTrue(suivi.verifier(50)[0])

def test_confirmation_des_grosses_demandes(self):

suivi = cout.SuiviCout(seuil=1000)

self.assertEqual(suivi.verifier(2000, interactif=True, demander=lambda q: "n"), (False, "Demande annulée."))

self.assertTrue(suivi.verifier(2000, interactif=True, demander=lambda q: "")[0])

self.assertTrue(suivi.verifier(500, interactif=True, demander=lambda q: "n")[0])   # sous le seuil

self.assertTrue(suivi.verifier(2000, interactif=False)[0])                          # jamais de question dans un script

self.assertTrue(suivi.verifier(2000, confirmer=False, interactif=True, demander=lambda q: "n")[0])

def test_compteurs(self):

suivi = cout.SuiviCout()

suivi.ajouter(120)

suivi.ajouter(None)

suivi.ajouter("abc")

suivi.ajouter_estimation("a" * 40, "b" * 40)

self.assertEqual((suivi.utilises, suivi.estimes, suivi.total), (120, 20, 140))

def test_lecture_de_l_environnement(self):

from unittest import mock

with mock.patch.dict("os.environ", {"DARKCODEX_MAX_TOKENS": "5000", "DARKCODEX_COST_CONFIRM": "800"}):

suivi = cout.SuiviCout.depuis_environnement()

self.assertEqual((suivi.limite, suivi.seuil), (5000, 800))

with mock.patch.dict("os.environ", {"DARKCODEX_MAX_TOKENS": "x"}, clear=True):

self.assertIsNone(cout.SuiviCout.depuis_environnement().limite)

if __name__ == "__main__":

unittest.main()

Vérification

python -m unittest discover -s tests : tout doit passer.

## Q3 — Dictée vocale : /dicter

Objectif

Sur Termux, la commande termux-speech-to-text (fournie par le paquet termux-api) ouvre la reconnaissance vocale du téléphone et renvoie le texte. /dicter la lance, montre ce qui a été compris et laisse choisir : envoyer, annuler ou corriger. Si Termux:API n'est pas installé, un message explique comment l'installer. Le texte dicté est ensuite traité comme s'il avait été tapé.

Fichiers

darkcodex/voix.py (nouveau), darkcodex/cli.py, tests/test_voix.py (nouveau)

Consignes

Crée darkcodex/voix.py avec le code ci-dessous.

Dans darkcodex/cli.py, change la ligne d'import en from . import cout, historique, navigateur, updater, voix.

Crée tests/test_voix.py. Le branchement dans le chat est fait en Q5.

darkcodex/voix.py

"""Dictée vocale sur Termux : /dicter (nécessite l'application Termux:API)."""

from __future__ import annotations

import shutil

import subprocess

from typing import Callable

COMMANDE = "termux-speech-to-text"

AIDE_INSTALLATION = (

"La dictée demande Termux:API : installe l'application « Termux:API » (même source que Termux), "

"puis tape dans Termux : pkg install termux-api"

)

def disponible() -> bool:

return shutil.which(COMMANDE) is not None

def nettoyer(texte: str) -> str:

return " ".join((texte or "").split())

def dicter(delai: int = 60) -> tuple[bool, str]:

"""Lance la reconnaissance vocale du téléphone. Retourne (succès, texte ou message d'erreur)."""

if not disponible():

return False, AIDE_INSTALLATION

try:

resultat = subprocess.run([COMMANDE], capture_output=True, text=True, timeout=delai)

except subprocess.TimeoutExpired:

return False, "Aucune voix détectée (délai dépassé)."

except OSError as exc:

return False, f"Dictée impossible : {exc}"

texte = nettoyer(resultat.stdout)

if resultat.returncode != 0 or not texte:

return False, "Aucun texte reconnu. Réessaie en parlant plus près du micro."

return True, texte

def dicter_et_valider(

dicteur: Callable[[], tuple[bool, str]] | None = None,

demander: Callable[[str], str] | None = None,

afficher: Callable[[str], None] = print,

) -> str | None:

"""Dicte, montre le texte compris, laisse envoyer / annuler / corriger. None = rien à envoyer."""

demander = demander or input

afficher("🎤 Parle maintenant...")

ok, texte = (dicteur or dicter)()

if not ok:

afficher(texte)

return None

afficher(f"J'ai compris : « {texte} »")

try:

choix = demander("Envoyer ? [O = oui, n = annuler, m = modifier] ").strip().lower()

if choix in {"n", "non"}:

return None

if choix in {"m", "modifier"}:

corrige = demander("Tape la version corrigée (Entrée = garder) : ").strip()

return corrige or texte

except (EOFError, KeyboardInterrupt):

return None

return texte

tests/test_voix.py

import subprocess

import unittest

from unittest import mock

from darkcodex import voix

class VoixTests(unittest.TestCase):

def test_sans_termux_api(self):

with mock.patch.object(voix.shutil, "which", return_value=None):

ok, message = voix.dicter()

self.assertFalse(ok)

self.assertIn("pkg install termux-api", message)

def test_texte_reconnu(self):

with mock.patch.object(voix.shutil, "which", return_value="/bin/termux-speech-to-text"), mock.patch.object(

voix.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="  bonjour   le monde \n")

):

self.assertEqual(voix.dicter(), (True, "bonjour le monde"))

def test_aucun_texte_ou_delai_depasse(self):

with mock.patch.object(voix.shutil, "which", return_value="/x"):

with mock.patch.object(voix.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="")):

self.assertFalse(voix.dicter()[0])

with mock.patch.object(voix.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):

self.assertIn("délai", voix.dicter()[1])

def test_validation_envoyer_annuler_modifier(self):

affichage = []

dicteur = lambda: (True, "ouvre le fichier app point py")

self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: "", affichage.append), "ouvre le fichier app point py")

self.assertIsNone(voix.dicter_et_valider(dicteur, lambda q: "n", affichage.append))

reponses = iter(["m", "lis app.py"])

self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: next(reponses), affichage.append), "lis app.py")

reponses = iter(["m", ""])

self.assertEqual(voix.dicter_et_valider(dicteur, lambda q: next(reponses), affichage.append), "ouvre le fichier app point py")

def test_echec_de_dictee(self):

self.assertIsNone(voix.dicter_et_valider(lambda: (False, "erreur"), lambda q: "o", lambda t: None))

if __name__ == "__main__":

unittest.main()

## Q4 — Parrainage (serveur et base de données)

Objectif

Chaque utilisateur a un code DK-XXXXXX, créé à la première demande. Un nouvel utilisateur l'entre une fois : lui et son parrain reçoivent chacun un bonus. Toute la logique est dans une fonction SQL unique (donc une seule transaction : les deux comptes sont crédités ou aucun). Elle refuse : un code inconnu, son propre code, un compte qui a déjà été parrainé, un parrain ayant atteint le maximum de filleuls, un compte vieux de plus de quelques jours, et le parrainage croisé entre deux comptes. Valeurs par défaut modifiables sur Render : PARRAINAGE_BONUS (2000), PARRAINAGE_MAX_FILLEULS (20), PARRAINAGE_JOURS_MAX (7).

Fichiers

supabase_parrainage.sql (nouveau), server/device.py, server/main.py, tests/test_parrainage.py (nouveau)

Consignes

Crée supabase_parrainage.sql avec le code ci-dessous (le propriétaire l'exécutera dans Supabase). Les deux lignes revoke et grant à la fin sont essentielles : sans elles, n'importe qui pourrait s'offrir des tokens. Ne les retire pas.

Dans server/device.py, ajoute le bloc ci-dessous à la fin du fichier.

Dans server/main.py, ajoute import re avec les autres imports, puis colle le bloc de routes ci-dessous avant le commentaire « Fichiers statiques du site » (le montage de /static doit rester la toute dernière chose du fichier).

Crée tests/test_parrainage.py.

supabase_parrainage.sql

-- DarkCodex — Parrainage

-- A exécuter UNE FOIS dans le Supabase SQL Editor (après supabase_login.sql). Peut être relancé sans danger.

alter table public.profils add column if not exists code_parrainage text unique;

alter table public.profils add column if not exists parraine_par uuid references auth.users(id);

create table if not exists public.parrainages (

id uuid default gen_random_uuid() primary key,

parrain_id uuid not null references auth.users(id),

filleul_id uuid not null unique references auth.users(id),  -- un compte ne peut être parrainé qu'une fois

bonus bigint not null,

cree_a timestamp with time zone not null default now()

);

-- Seul le serveur (service_role) lit/écrit : RLS activé, aucune policy.

alter table public.parrainages enable row level security;

-- Tout se passe dans UNE fonction (= une transaction) : les deux comptes sont crédités ou aucun.

create or replace function public.utiliser_code_parrainage(

p_user_id uuid,

p_code text,

p_bonus bigint,

p_max_filleuls int,

p_jours int

)

returns text

language plpgsql

security definer

set search_path = public

as $$

declare

v_parrain uuid;

v_deja uuid;

v_cree timestamp with time zone;

v_nb int;

begin

select id into v_parrain from public.profils where code_parrainage = upper(p_code);

if v_parrain is null then return 'code_inconnu'; end if;

if v_parrain = p_user_id then return 'code_personnel'; end if;

-- Verrou sur la ligne du filleul : deux demandes simultanées ne peuvent pas toutes deux réussir.

select parraine_par into v_deja from public.profils where id = p_user_id for update;

if not found then return 'profil_introuvable'; end if;

if v_deja is not null then return 'deja_parraine'; end if;

select created_at into v_cree from auth.users where id = p_user_id;

if v_cree is null or v_cree < now() - make_interval(days => p_jours) then

return 'compte_trop_ancien';  -- le code sert à l'inscription, pas à un vieux compte

end if;

if exists (select 1 from public.parrainages where parrain_id = p_user_id and filleul_id = v_parrain) then

return 'code_invalide';       -- pas de parrainage croisé entre deux comptes

end if;

select count(*) into v_nb from public.parrainages where parrain_id = v_parrain;

if v_nb >= p_max_filleuls then return 'parrain_complet'; end if;

update public.profils set parraine_par = v_parrain, tokens_solde = tokens_solde + p_bonus where id = p_user_id;

update public.profils set tokens_solde = tokens_solde + p_bonus where id = v_parrain;

insert into public.parrainages (parrain_id, filleul_id, bonus) values (v_parrain, p_user_id, p_bonus);

return 'ok';

end;

$$;

-- IMPORTANT : sans ces deux lignes, n'importe qui pourrait s'offrir des tokens via la clé « anon ».

revoke all on function public.utiliser_code_parrainage(uuid, text, bigint, int, int) from public, anon, authenticated;

grant execute on function public.utiliser_code_parrainage(uuid, text, bigint, int, int) to service_role;

server/device.py : à la fin du fichier

# ── Parrainage (voir supabase_parrainage.sql) ───────────────────

def generer_code_parrainage() -> str:

"""Format DK-XXXXXX (6 caractères, même alphabet sans ambiguïté que les codes de connexion)."""

return "DK-" + "".join(secrets.choice(ALPHABET_CODE) for _ in range(6))

def _code_parrainage_existant(user_id: str) -> str:

response = requests.get(

rest_url("profils", {"id": f"eq.{user_id}", "select": "code_parrainage", "limit": "1"}),

headers=service_headers(),

timeout=20,

)

if not response.ok:

raise RuntimeError(f"Supabase profils (select): HTTP {response.status_code}")

rows = response.json()

return str((rows[0].get("code_parrainage") if rows else "") or "")

def obtenir_code_parrainage(user_id: str) -> str:

"""Retourne le code de l'utilisateur, en le créant à la première demande."""

existant = _code_parrainage_existant(user_id)

if existant:

return existant

for _ in range(5):  # plusieurs essais au cas (très rare) où le code existerait déjà

code = generer_code_parrainage()

response = requests.patch(

rest_url("profils", {"id": f"eq.{user_id}", "code_parrainage": "is.null"}),

headers=service_headers({"Prefer": "return=representation"}),

json={"code_parrainage": code},

timeout=20,

)

if response.status_code == 409:  # code déjà pris par quelqu'un d'autre

continue

if not response.ok:

raise RuntimeError(f"Supabase profils (update): HTTP {response.status_code} {response.text[:200]}")

if response.json():

return code

return _code_parrainage_existant(user_id) or code  # une autre requête a créé le code entre-temps

raise RuntimeError("Impossible de créer un code de parrainage.")

def stats_parrainage(user_id: str) -> dict[str, int]:

response = requests.get(

rest_url("parrainages", {"parrain_id": f"eq.{user_id}", "select": "bonus"}),

headers=service_headers(),

timeout=20,

)

if not response.ok:

raise RuntimeError(f"Supabase parrainages (select): HTTP {response.status_code}")

rows = response.json()

rows = rows if isinstance(rows, list) else []

return {"filleuls": len(rows), "tokens_gagnes": sum(int(r.get("bonus") or 0) for r in rows)}

def utiliser_parrainage(user_id: str, code: str, bonus: int, max_filleuls: int, jours: int) -> str:

"""Appelle la fonction SQL atomique. Retourne 'ok' ou le motif du refus."""

response = requests.post(

f"{supabase_url()}/rest/v1/rpc/utiliser_code_parrainage",

headers=service_headers(),

json={"p_user_id": user_id, "p_code": code, "p_bonus": int(bonus), "p_max_filleuls": int(max_filleuls), "p_jours": int(jours)},

timeout=20,

)

if not response.ok:

raise RuntimeError(f"Supabase utiliser_code_parrainage: HTTP {response.status_code} {response.text[:200]}")

resultat = response.json()

return str(resultat if not isinstance(resultat, list) else (resultat[0] if resultat else ""))

server/main.py : avant « # Fichiers statiques du site »

# ── Parrainage ──────────────────────────────────────────────────

PARRAINAGE_REFUS = {

"code_inconnu": (404, "Code de parrainage inconnu."),

"code_personnel": (400, "Tu ne peux pas utiliser ton propre code."),

"code_invalide": (400, "Ce code ne peut pas être utilisé."),

"deja_parraine": (409, "Tu as déjà utilisé un code de parrainage."),

"parrain_complet": (409, "Ce code a atteint son nombre maximum de filleuls."),

"compte_trop_ancien": (403, "Le code de parrainage doit être utilisé dans les premiers jours du compte."),

"profil_introuvable": (404, "Profil introuvable."),

}

def _entier_env(nom: str, defaut: int) -> int:

try:

return int(os.environ.get(nom, "").strip())

except ValueError:

return defaut

class ParrainageIn(BaseModel):

code: str

@app.get("/parrainage")

def parrainage(authorization: str | None = Header(None)) -> Any:

session = _utilisateur(authorization)

try:

code = device.obtenir_code_parrainage(session["user_id"])

stats = device.stats_parrainage(session["user_id"])

except Exception as exc:

return _erreur(exc)

return {"code": code, "bonus": _entier_env("PARRAINAGE_BONUS", 2000), **stats}

@app.post("/parrainage/utiliser")

def parrainage_utiliser(payload: ParrainageIn, authorization: str | None = Header(None)) -> Any:

session = _utilisateur(authorization)

code = payload.code.strip().upper()

if not re.fullmatch(r"DK-[A-Z0-9]{6}", code):

raise HTTPException(status_code=400, detail="Format de code invalide (exemple : DK-AB23CD).")

bonus = _entier_env("PARRAINAGE_BONUS", 2000)

try:

resultat = device.utiliser_parrainage(

session["user_id"], code, bonus,

_entier_env("PARRAINAGE_MAX_FILLEULS", 20), _entier_env("PARRAINAGE_JOURS_MAX", 7),

)

except Exception as exc:

return _erreur(exc)

if resultat == "ok":

return {"status": "ok", "tokens_ajoutes": bonus}

if resultat in PARRAINAGE_REFUS:

statut, message = PARRAINAGE_REFUS[resultat]

raise HTTPException(status_code=statut, detail=message)

return _erreur(RuntimeError("Réponse inattendue du parrainage."))

tests/test_parrainage.py

import os

import re

import sys

import unittest

from pathlib import Path

from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:

from fastapi.testclient import TestClient

from server import device as srv_device

from server import main as srv_main

except Exception:  # fastapi / httpx absents

TestClient = None

skip = unittest.skipIf(TestClient is None, "fastapi + httpx requis pour ces tests")

ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "service-secret"}

SESSION = {"user_id": "u1", "email": "a@b.c", "nom": "Awa", "tokens_solde": 5, "tokens_utilises": 0, "plan": "gratuit"}

def reponse(ok=True, statut=200, json=None):

r = mock.Mock()

r.ok, r.status_code, r.text = ok, statut, ""

r.json.return_value = json

return r

@skip

class ParrainageDeviceTests(unittest.TestCase):

def test_format_du_code(self):

for _ in range(50):

self.assertRegex(srv_device.generer_code_parrainage(), r"^DK-[A-HJ-NP-Z2-9]{6}$")

def test_code_existant_retourne_sans_ecriture(self):

with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"code_parrainage": "DK-AAAAAA"}])), \

mock.patch.object(srv_device.requests, "patch") as patch:

self.assertEqual(srv_device.obtenir_code_parrainage("u1"), "DK-AAAAAA")

patch.assert_not_called()

def test_creation_avec_une_collision(self):

with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"code_parrainage": None}])), \

mock.patch.object(srv_device.requests, "patch", side_effect=[reponse(False, 409, None), reponse(json=[{"id": "u1"}])]) as patch:

code = srv_device.obtenir_code_parrainage("u1")

self.assertRegex(code, r"^DK-")

self.assertEqual(patch.call_count, 2)

self.assertIn("code_parrainage=is.null", patch.call_args.args[0])

def test_utiliser_parrainage_lit_la_reponse_sql(self):

with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "post", return_value=reponse(json="ok")) as post:

self.assertEqual(srv_device.utiliser_parrainage("u1", "DK-AAAAAA", 2000, 20, 7), "ok")

self.assertEqual(post.call_args.kwargs["json"]["p_bonus"], 2000)

self.assertTrue(post.call_args.args[0].endswith("/rpc/utiliser_code_parrainage"))

def test_stats(self):

with mock.patch.dict(os.environ, ENV), mock.patch.object(srv_device.requests, "get", return_value=reponse(json=[{"bonus": 2000}, {"bonus": 2000}])):

self.assertEqual(srv_device.stats_parrainage("u1"), {"filleuls": 2, "tokens_gagnes": 4000})

@skip

class ParrainageRoutesTests(unittest.TestCase):

def setUp(self):

self.client = TestClient(srv_main.app)

self.entetes = {"Authorization": "Bearer dkx_abc"}

def test_mon_code(self):

with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), \

mock.patch.object(srv_device, "obtenir_code_parrainage", return_value="DK-AB23CD"), \

mock.patch.object(srv_device, "stats_parrainage", return_value={"filleuls": 1, "tokens_gagnes": 2000}):

donnees = self.client.get("/parrainage", headers=self.entetes).json()

self.assertEqual((donnees["code"], donnees["filleuls"], donnees["bonus"]), ("DK-AB23CD", 1, 2000))

def test_utiliser_un_code(self):

with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), \

mock.patch.object(srv_device, "utiliser_parrainage", return_value="ok") as utiliser, \

mock.patch.dict(os.environ, {"PARRAINAGE_BONUS": "3000"}):

r = self.client.post("/parrainage/utiliser", json={"code": " dk-ab23cd "}, headers=self.entetes)

self.assertEqual((r.status_code, r.json()["tokens_ajoutes"]), (200, 3000))

self.assertEqual(utiliser.call_args.args[:3], ("u1", "DK-AB23CD", 3000))

def test_refus_expliques(self):

attendus = {"code_inconnu": 404, "code_personnel": 400, "deja_parraine": 409, "parrain_complet": 409, "compte_trop_ancien": 403, "code_invalide": 400}

for motif, statut in attendus.items():

with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(srv_device, "utiliser_parrainage", return_value=motif):

r = self.client.post("/parrainage/utiliser", json={"code": "DK-AB23CD"}, headers=self.entetes)

self.assertEqual(r.status_code, statut, motif)

self.assertTrue(r.json()["detail"])

def test_format_invalide_n_appelle_pas_la_base(self):

with mock.patch.object(srv_device, "verifier_token", return_value=SESSION), mock.patch.object(srv_device, "utiliser_parrainage") as utiliser:

r = self.client.post("/parrainage/utiliser", json={"code": "n'importe quoi"}, headers=self.entetes)

self.assertEqual(r.status_code, 400)

utiliser.assert_not_called()

def test_sans_jeton(self):

self.assertEqual(self.client.get("/parrainage").status_code, 401)

if __name__ == "__main__":

unittest.main()

Vérification

python -m unittest discover -s tests : tout doit passer. Ne lance pas le SQL toi-même : c'est le propriétaire qui l'exécute.

## Q5 — Les commandes dans le chat

Objectif

Branche tout dans la boucle du chat : les commandes /historique, /annuler, /diff, /cout, /limite et /parrainer (via une fonction handle_nouvelles_commandes), /dicter (le texte dicté remplace la ligne saisie), la vérification de la limite de session et le comptage des tokens à chaque message, et l'aide /help.

Fichiers

darkcodex/cli.py, tests/test_nouvelles_commandes.py (nouveau)

Consignes

Dans darkcodex/cli.py, ajoute les fonctions du premier bloc ci-dessous juste avant def cmd_chat(.

Dans cmd_chat, applique les cinq modifications du second bloc, dans l'ordre. Ne change rien d'autre dans la boucle.

Vérifie qu'aucune commande existante ne s'appelle déjà /diff, /cout, /annuler, /historique, /limite, /dicter ou /parrainer. S'il y a un conflit, arrête-toi et signale-le.

Crée tests/test_nouvelles_commandes.py.

darkcodex/cli.py : avant def cmd_chat(

def commande_historique(cwd: Path) -> None:

print(historique.formater(historique.lister(cwd, limite=10)))

def commande_annuler(cwd: Path) -> None:

entree = historique.prochaine_annulation(cwd)

if entree is None:

print("Rien à annuler.")

return

action = "restaurer" if entree.get("existait") else "supprimer le fichier créé"

if not confirm_user(f"Annuler la dernière modification ({action} : {entree['chemin']}) ?"):

print("Annulation abandonnée.")

return

ok, message = historique.annuler(cwd)

print(("✅ " if ok else "⛔ ") + message)

def commande_diff(line: str, cwd: Path) -> None:

fichier = line.removeprefix("/diff").strip() or None

ok, texte = historique.diff_derniere(cwd, fichier)

print(historique.colorier_diff(texte) if ok else texte)

def commande_cout(session: dict | None) -> None:

suivi = cout.SESSION

lignes = ["💰 Coût de la session", f"Tokens utilisés (mesurés) : {fmt_tokens(suivi.utilises)}"]

if suivi.estimes:

lignes.append(f"Tokens estimés (mode local) : ~{fmt_tokens(suivi.estimes)}")

lignes.append("Limite de session : " + (fmt_tokens(suivi.limite) if suivi.limite else "aucune"))

lignes.append(f"Confirmation au-delà de : ~{fmt_tokens(suivi.seuil)} tokens par demande")

if session:

lignes.append(f"Solde du compte : {fmt_tokens(session.get('tokens_solde'))}")

print(box(*lignes))

def commande_limite(line: str) -> None:

valeur = line.removeprefix("/limite").strip()

if not valeur:

limite = cout.SESSION.limite

print(f"Limite de session : {fmt_tokens(limite)} tokens." if limite else "Aucune limite de session. Exemple : /limite 20000")

return

print(cout.SESSION.definir_limite(valeur)[1])

def commande_parrainer(line: str, session: dict | None) -> None:

if not session:

print("Connecte-toi d'abord avec /login.")

return

morceaux = line.split()

if len(morceaux) >= 2 and morceaux[1].lower() == "utiliser":

if len(morceaux) < 3:

print("Usage : /parrainer utiliser DK-XXXXXX")

return

status, data = api_call("/parrainage/utiliser", method="POST", payload={"code": morceaux[2]}, token=session["token"], timeout=30)

if status == 200:

ajoutes = int(data.get("tokens_ajoutes") or 0)

session["tokens_solde"] = int(session.get("tokens_solde") or 0) + ajoutes

save_auth(session)

print(f"✅ Code accepté : +{fmt_tokens(ajoutes)} tokens pour toi et pour ton parrain !")

else:

print(f"⛔ {api_error_text(data)}")

return

status, data = api_call("/parrainage", token=session["token"], timeout=30)

if status != 200:

print(f"⛔ {api_error_text(data)}")

return

print(box(

"🎁 Parrainage",

f"Ton code : {data.get('code')}",

f"Chaque filleul : +{fmt_tokens(data.get('bonus'))} tokens pour lui ET pour toi",

f"Filleuls : {data.get('filleuls', 0)} | Tokens gagnés : {fmt_tokens(data.get('tokens_gagnes'))}",

f"Ton ami tape : /parrainer utiliser {data.get('code')}",

))

def handle_nouvelles_commandes(line: str, cwd: Path, session: dict | None) -> bool:

commande = line.split(" ", 1)[0]

if commande == "/historique":

commande_historique(cwd)

elif commande == "/annuler":

commande_annuler(cwd)

elif commande == "/diff":

commande_diff(line, cwd)

elif commande == "/cout":

commande_cout(session)

elif commande == "/limite":

commande_limite(line)

elif commande == "/parrainer":

commande_parrainer(line, session)

else:

return False

return True

darkcodex/cli.py : dans cmd_chat()

# 1) Dans cmd_chat(), boucle « while True » : remplace les deux premières conditions après la saisie

#    (« if line in {"/exit", "/quit"}: return 0 » puis le début de « /mode ») par :

if line in {"/exit", "/quit"}:

return 0

if line == "/dicter":

texte_dicte = voix.dicter_et_valider()

if not texte_dicte:

continue

line = texte_dicte  # le texte dicté est traité comme s'il avait été tapé

if handle_nouvelles_commandes(line, cwd, session):

continue

if line == "/mode" or line.startswith("/mode "):

# 2) Toujours dans la boucle, juste AVANT « store.add_message(session_id, "user", line) » :

ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(line), confirmer=False)

if not ok_cout:

print(f"\n{C_RED}{message_cout}{C_END}\n")

continue

# 3) Après la réponse du serveur, juste après « tokens_left = data.get("solde_restant") » :

cout.SESSION.ajouter(tokens_used)

# 4) Dans la branche « mode local » (else), juste après « code, output = run_provider(...) » :

if code == 0:

cout.SESSION.ajouter_estimation(f"{context}{line}", output)

# 5) Dans l'aide « /help » : avant le titre « MÉMOIRE & DIVERS », ajoute :

print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ HISTORIQUE & COÛT ━━━━━━━━━━━━{C_END}")

print(f"  {C_CYAN}/historique{C_END}               → dernières modifications de fichiers")

print(f"  {C_CYAN}/annuler{C_END}                  → annuler la dernière modification")

print(f"  {C_CYAN}/diff [fichier]{C_END}           → voir ce qui a changé")

print(f"  {C_CYAN}/cout{C_END}                     → tokens utilisés dans la session")

print(f"  {C_CYAN}/limite [nombre|off]{C_END}      → plafond de tokens pour la session")

print(f"  {C_CYAN}/dicter{C_END}                   → parler au lieu d'écrire (Termux:API)")

print(f"  {C_CYAN}/parrainer{C_END}                → ton code de parrainage (+ tokens offerts)")

tests/test_nouvelles_commandes.py

import contextlib

import io

import tempfile

import unittest

from pathlib import Path

from unittest import mock

from darkcodex import agent_fichiers, cli, cout, historique

def sortie(fonction, *args):

tampon = io.StringIO()

with contextlib.redirect_stdout(tampon):

resultat = fonction(*args)

return resultat, tampon.getvalue()

class FauxCtx:

def __init__(self, cwd):

self.cwd = cwd

def note(self, texte=""):

pass

def confirm_edit(self, question):

return True

def confirm_always(self, question):

return True

def note_modification(self, chemin, action):

pass

class CommandesTests(unittest.TestCase):

def setUp(self):

self.tmp = tempfile.TemporaryDirectory()

self.cwd = Path(self.tmp.name)

def tearDown(self):

self.tmp.cleanup()

def test_commandes_inconnues_ne_sont_pas_prises(self):

self.assertFalse(cli.handle_nouvelles_commandes("/lire x.py", self.cwd, None))

def test_historique_annuler_diff(self):

fichier = self.cwd / "a.py"

fichier.write_text("v1\n")

historique.enregistrer(self.cwd, fichier)

fichier.write_text("v2\n")

_, texte = sortie(cli.handle_nouvelles_commandes, "/historique", self.cwd, None)

self.assertIn("a.py", texte)

_, texte = sortie(cli.handle_nouvelles_commandes, "/diff", self.cwd, None)

self.assertIn("+v2", texte)

with mock.patch.object(cli, "confirm_user", return_value=True):

_, texte = sortie(cli.handle_nouvelles_commandes, "/annuler", self.cwd, None)

self.assertIn("Restauré", texte)

self.assertEqual(fichier.read_text(), "v1\n")

def test_annuler_refuse_ne_change_rien(self):

fichier = self.cwd / "a.py"

fichier.write_text("v1")

historique.enregistrer(self.cwd, fichier)

fichier.write_text("v2")

with mock.patch.object(cli, "confirm_user", return_value=False):

sortie(cli.handle_nouvelles_commandes, "/annuler", self.cwd, None)

self.assertEqual(fichier.read_text(), "v2")

def test_appliquer_fichier_enregistre_l_historique(self):

fichier = self.cwd / "m.py"

fichier.write_text("ancien\n")

self.assertTrue(agent_fichiers.appliquer_fichier(fichier, "nouveau\n", FauxCtx(self.cwd)))

entrees = historique.lister(self.cwd)

self.assertEqual(entrees[0]["chemin"], "m.py")

ok, _ = historique.annuler(self.cwd)

self.assertTrue(ok)

self.assertEqual(fichier.read_text(), "ancien\n")

def test_cout_et_limite(self):

with mock.patch.object(cli.cout, "SESSION", cout.SuiviCout()):

_, texte = sortie(cli.handle_nouvelles_commandes, "/limite 20000", self.cwd, None)

self.assertIn("20 000", texte)

cli.cout.SESSION.ajouter(1234)

_, texte = sortie(cli.handle_nouvelles_commandes, "/cout", self.cwd, {"tokens_solde": 4000})

self.assertIn("1 234", texte)

self.assertIn("20 000", texte)

self.assertIn("4 000", texte)

_, texte = sortie(cli.handle_nouvelles_commandes, "/limite off", self.cwd, None)

self.assertIn("supprimée", texte)

def test_ai_answer_connecte_met_a_jour_solde_et_cout(self):

session = {"token": "dkx_abc", "tokens_solde": 100}

with mock.patch.object(cli.cout, "SESSION", cout.SuiviCout()), mock.patch.object(

cli, "load_auth", return_value=session

), mock.patch.object(cli, "server_answer", return_value=(0, "réponse", {"tokens_utilises": 10, "solde_restant": 90})), \

mock.patch.object(cli, "save_auth") as sauver, mock.patch.object(cli, "show_token_line") as ligne:

resultat = cli.ai_answer("gemini", "bonjour", {}, self.cwd)

utilises = cli.cout.SESSION.utilises

self.assertEqual(resultat, (0, "réponse"))

self.assertEqual(utilises, 10)

self.assertEqual(sauver.call_args.args[0]["tokens_solde"], 90)

ligne.assert_called_once_with(10, 90)

def test_ai_answer_respecte_la_limite(self):

suivi = cout.SuiviCout(limite=10)

suivi.ajouter(10)

with mock.patch.object(cli.cout, "SESSION", suivi), mock.patch.object(

cli, "load_auth", return_value={"token": "dkx_abc"}

), mock.patch.object(cli, "server_answer") as serveur:

code, message = cli.ai_answer("gemini", "bonjour", {}, self.cwd)

self.assertEqual(code, 1)

self.assertIn("Limite de session", message)

serveur.assert_not_called()

def test_parrainer(self):

session = {"token": "dkx_abc", "tokens_solde": 100}

_, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer", self.cwd, None)

self.assertIn("/login", texte)

with mock.patch.object(cli, "api_call", return_value=(200, {"code": "DK-AB23CD", "bonus": 2000, "filleuls": 3, "tokens_gagnes": 6000})):

_, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer", self.cwd, session)

self.assertIn("DK-AB23CD", texte)

with mock.patch.object(cli, "api_call", return_value=(200, {"status": "ok", "tokens_ajoutes": 2000})) as appel, \

mock.patch.object(cli, "save_auth") as sauver:

_, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer utiliser DK-AB23CD", self.cwd, session)

self.assertIn("Code accepté", texte)

self.assertEqual(appel.call_args.kwargs["payload"], {"code": "DK-AB23CD"})

self.assertEqual(sauver.call_args.args[0]["tokens_solde"], 2100)

with mock.patch.object(cli, "api_call", return_value=(409, {"detail": "Tu as déjà utilisé un code de parrainage."})):

_, texte = sortie(cli.handle_nouvelles_commandes, "/parrainer utiliser DK-AB23CD", self.cwd, session)

self.assertIn("déjà utilisé", texte)

if __name__ == "__main__":

unittest.main()

Vérification

python -m unittest discover -s tests : tout doit passer (257 tests sur la version de référence ; le nombre exact peut varier un peu).

# 4. Actions du propriétaire (pas de code)

Quand Codex a fini Q4, ouvre Supabase, SQL Editor, colle le contenu de supabase_parrainage.sql et clique sur Run. Si une erreur apparaît, envoie-la telle quelle à Claude.

Render, Environment (facultatif) : PARRAINAGE_BONUS (tokens offerts à chacun), PARRAINAGE_MAX_FILLEULS, PARRAINAGE_JOURS_MAX. Sans ces variables, les valeurs par défaut s'appliquent.

Pour la dictée : installe l'application Termux:API (de la même source que Termux), puis tape dans Termux : pkg install termux-api. Accorde la permission du micro quand le téléphone le demande.

Envoie le code sur GitHub et attends que Render ait fini de redéployer.

# 5. Vérification de bout en bout (Q6)

Historique : demande à DarkCodex de corriger un fichier de test (/corriger fichier.py). Tape /historique (la modification apparaît), /diff (tu vois les lignes changées), /annuler (réponds oui : le fichier revient à l'ancienne version), puis /annuler une seconde fois : la modification est rétablie.

Coût : tape /cout, puis /limite 1000 et pose une longue question : elle doit être refusée avec un message clair. /limite off retire le plafond. Lance /resume . dans un gros dossier : une question de confirmation avec une estimation doit apparaître.

Solde : après /lire fichier.py, la ligne « Tokens utilisés / Restants » doit maintenant s'afficher et /solde doit être à jour (c'était le bug).

Dictée (sur le téléphone) : tape /dicter, parle, vérifie le texte proposé, réponds « o ». Si rien ne se passe, vérifie l'installation de Termux:API et la permission du micro.

Parrainage : avec un compte A, tape /parrainer et note le code. Avec un compte B créé il y a moins de 7 jours, tape /parrainer utiliser CODE : les deux soldes augmentent (/solde). Refais la même commande avec B : « déjà utilisé un code ». Essaie aussi ton propre code : refusé.

Points de vigilance

Les trois scripts SQL (appareils, paiements, parrainage) ont été vérifiés avec un analyseur PostgreSQL pour la syntaxe, mais pas exécutés sur un vrai Supabase : exécute-les avec attention et signale toute erreur.

La dictée a été testée avec des commandes simulées, pas sur un vrai téléphone. La langue reconnue dépend des réglages du téléphone.

L'estimation de tokens (4 caractères = 1 token) est approximative. Dans le chat, la limite de session tient compte du message tapé et des tokens déjà consommés, pas de tout le contexte du projet : elle peut être dépassée d'une réponse.

L'historique ne couvre que les fichiers modifiés par DarkCodex (pas ceux modifiés par une commande lancée avec /run), jusqu'à 200 modifications et 2 Mo par fichier.

Le parrainage limite les abus (20 filleuls, compte récent, pas de parrainage croisé) mais quelqu'un peut toujours créer plusieurs faux comptes avec des emails différents. Activer « Confirm email » dans Supabase et garder un bonus raisonnable réduit ce risque.

| Commandes | Ce que ça fait |

| /historique, /annuler, /diff | Avant chaque modification d'un fichier, DarkCodex garde l'ancienne version (jusqu'à 200 modifications). /annuler restaure, /diff montre ce qui a changé. Un second /annuler rétablit la modification. |

| /cout, /limite | Affiche les tokens utilisés dans la session, permet de fixer un plafond, et demande confirmation avant une grosse demande (estimation à plus de 5 000 tokens). |

| /dicter | Dicte un message à la voix avec la reconnaissance vocale du téléphone (Termux:API). Le texte compris est montré avant l'envoi. |

| /parrainer | Donne un code de parrainage. Quand un nouvel utilisateur l'utilise, lui et son parrain reçoivent chacun des tokens (2 000 par défaut). |

| Partie | Objectif | Fichiers |

| Q1 | Historique : sauvegardes avant modification | darkcodex/historique.py (nouveau), darkcodex/agent_fichiers.py, darkcodex/cli.py, tests/test_historique.py |

| Q2 | Coût en tokens + correction du bug du solde | darkcodex/cout.py (nouveau), darkcodex/cli.py, tests/test_cout.py |

| Q3 | Dictée vocale | darkcodex/voix.py (nouveau), darkcodex/cli.py, tests/test_voix.py |

| Q4 | Parrainage (serveur et base de données) | supabase_parrainage.sql (nouveau), server/device.py, server/main.py, tests/test_parrainage.py |

| Q5 | Les commandes dans le chat (/historique, /cout, /parrainer...) | darkcodex/cli.py, tests/test_nouvelles_commandes.py |

| Q6 | Vérification de bout en bout | aucun fichier (checklist) |
