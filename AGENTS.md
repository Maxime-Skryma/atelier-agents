# Développer l'Atelier

Ces consignes servent à **modifier le code de l'Atelier** (Codex, Antigravity, Claude Code lancés dans ce dossier).
Si ton répertoire de travail est sous `data/` (une mission lancée par l'Atelier), ignore entièrement ce fichier : suis uniquement les consignes de la mission.

## Ce qu'est l'Atelier

Dashboard web personnel qui compose des équipes d'agents (Claude Code, Codex, Gemini · Antigravity) et leur confie des missions. Un seul utilisateur de confiance, sur une VM dédiée (Onyxia ou autre), accessible en local, par tunnel SSH ou derrière un proxy.

- `server.py` : application WSGI complète (stockage SQLite, routes `/api/…`, missions, construction des consignes, lancement des assistants). Servie par Gunicorn, 1 worker, 8 threads.
- `antigravity_adapter.py` : commande `agy`, protocole `stream-json`, contrôle des permissions Antigravity, verrou lecture seule.
- `formats.py` : formats de rendu (PDF, Word, PowerPoint, LaTeX) ; consignes à l'agent final et conversion par Pandoc (`pypandoc_binary`) et XeLaTeX.
- `i18n.py` : langue de l’interface (`L(fr, en)`), réglée par `settings.json` (`language`).
- `verification.py` : vérification déterministe, sans IA, des sources citées (liens, DOI, arXiv, fichiers). En cas d'échec, `Atelier.verify_step` renvoie les erreurs à l'agent (`MAX_FIXES` corrections au plus).
- `static/` : interface en un seul fichier `app.js` (JavaScript sans framework ni build) + `style.css` + `index.html`.
- `tests/` : `unittest`, avec des exécutables simulés. Aucun appel à un vrai modèle.
- `scripts/start.sh` : installation et démarrage du service (`onyxia.sh` en est un alias). `scripts/reload.sh` : rechargement après modification.
- `data/` : **données de l'utilisateur** (base `atelier.sqlite3`, `settings.json` avec le hash du mot de passe, missions, journaux). Ne jamais modifier, supprimer ou committer.

Gemini CLI a été remplacé par Antigravity : le code fait foi.

## Règles de travail

1. **Sauvegarde avant de modifier** : en dehors d’un dépôt git, copie les fichiers touchés dans `../atelier-backups/code-avant-<sujet>/` ; dans un dépôt git, travaille sur une branche.
2. **Style** : reste fidèle au code existant. Python et JavaScript compacts (plusieurs instructions par ligne, peu de commentaires, commentaires en anglais), pas de nouvelle dépendance. Interface bilingue : chaque texte visible existe en français et en anglais, `t('français','English')` dans `static/app.js`, `L('français', 'English')` en Python (`i18n.py`) ; en français, apostrophes typographiques (’) et guillemets « ». Les consignes envoyées aux agents sont en anglais uniquement ; la langue des livrables suit `m['language']`.
3. **Tests** : ajoute un test dans `tests/` pour chaque comportement nouveau, puis lance
   `.venv/bin/python -m unittest discover -s tests` (pytest n'est pas installé) et `node --check static/app.js`.
4. **Recharger** : le serveur garde l'ancien code en mémoire. Après toute modification de `server.py` ou `antigravity_adapter.py`, lance `bash scripts/reload.sh` : il relance les tests, refuse si une mission tourne, puis recharge sans couper le service. Les fichiers de `static/` sont relus à chaque requête : il suffit de recharger la page du navigateur.
5. **Changements de données** : un changement de format des objets stockés doit rester compatible avec les missions existantes (`m.get(...)`), ou passer par une migration au démarrage avec sauvegarde de la base, comme `migrate_providers`.
6. Termine par un résumé : fichiers modifiés, tests lancés et résultat, ce qui n'a pas été vérifié.

## Garde-fous à ne pas affaiblir

Ne modifie pas ces mécanismes sans demande explicite de l'utilisateur, et signale-le s'il le demande :

- authentification (mot de passe haché, cookie de session `HttpOnly`/`SameSite=Strict`), contrôle CSRF par l'en-tête `X-Atelier-Request` et l'origine ;
- `safe_file` (pas de sortie du dossier, pas de liens symboliques) ;
- permissions des assistants dans `Atelier.command` et `antigravity_adapter.permission_problem` ; le verrou `read_only` et la comparaison `snapshot` des missions en lecture seule ;
- `agent_env` : les assistants utilisent les abonnements connectés, sans hériter des clés d'API ;
- le serveur écoute uniquement sur `127.0.0.1:8765`.

N'affiche, ne copie et ne transmets jamais le contenu de `data/settings.json`, des sessions (`~/.codex`, `~/.gemini`, `~/.claude`) ni de jetons.
