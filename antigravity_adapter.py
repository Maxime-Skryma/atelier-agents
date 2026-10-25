"""Antigravity CLI (agy) bridge. Authentication stays with the VM's Google sign-in."""
import contextlib
import json
import os
from pathlib import Path

from i18n import L
import re
import stat
import subprocess

BINARY = 'agy'
# Documented headless defaults: workspace files are allowed, everything else is soft-denied.
# These settings would widen that, so Atelier refuses to run rather than lose per-mission limits.
UNSAFE_MODES = {'always-proceed', 'proceed-in-sandbox'}
UNSAFE_ALLOW = re.compile(r'^\s*(command|unsandboxed|mcp|execute_url|read_url)\s*\(')


def config_root(environ=None):
    env = os.environ if environ is None else environ
    return Path(env.get('HOME') or str(Path.home())) / '.gemini'


def _load(path):
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    return json.loads(text) if text.strip() else {}


def permission_problem(environ=None):
    """Return an explanation when user settings would bypass mission permissions."""
    root = config_root(environ)
    try:
        sources = [(L('réglages Antigravity', 'Antigravity settings'), _load(root / 'antigravity-cli' / 'settings.json')),
                   (L('réglages partagés', 'shared settings'), _load(root / 'config' / 'config.json'))]
        for path in sorted((root / 'config' / 'projects').glob('*.json')):
            sources.append((L('projet Antigravity ', 'Antigravity project ') + path.stem, _load(path)))
    except (OSError, ValueError):
        return L('Les réglages Antigravity ne peuvent pas être lus.', 'Antigravity settings cannot be read.')
    for label, data in sources:
        if not isinstance(data, dict):
            return L('Les ' + label + ' sont illisibles.', 'The ' + label + ' are unreadable.')
        if data.get('toolPermission') in UNSAFE_MODES:
            return L('Les ' + label + ' approuvent automatiquement les actions. Rétablissez « request-review ».', 'The ' + label + ' approve actions automatically. Restore "request-review".')
        if data.get('allowNonWorkspaceAccess'):
            return L('Les ' + label + ' autorisent l’accès hors du dossier de travail.', 'The ' + label + ' allow access outside the working folder.')
        permissions = data.get('permissions') or {}
        allow = permissions.get('allow', []) if isinstance(permissions, dict) else []
        if any(isinstance(rule, str) and (UNSAFE_ALLOW.match(rule) or rule.strip().endswith('(*)')) for rule in allow):
            return L('Les ' + label + ' pré-autorisent le terminal, MCP ou le web. Retirez ces règles « allow » pour Atelier.', 'The ' + label + ' pre-approve the terminal, MCP or the web. Remove these "allow" rules for Atelier.')
        for key, value in data.items():
            if 'ermission' in key and key != 'permissions' and value not in (None, {}, [], '', 'request-review', 'strict'):
                return L('Les ' + label + ' contiennent des permissions supplémentaires (' + key + ').', 'The ' + label + ' contain extra permissions (' + key + ').')
    return None


def command(binary, model, timeout_minutes, effort=None):
    problem = permission_problem()
    if problem:
        raise RuntimeError(problem)
    # Prompt goes through stdin as one stream-json user event; never on argv.
    args = [binary, '--print=', '--output-format', 'stream-json', '--input-format', 'stream-json',
            '--disable-slash-commands', '--print-timeout', '%dm' % timeout_minutes]
    if model:
        args.extend(['--model', model])
    if effort:
        args.extend(['--effort', effort])
    return args


def stdin_payload(prompt):
    return (json.dumps({'event': 'user', 'message': {'content': prompt}}, ensure_ascii=False) + '\n').encode()


@contextlib.contextmanager
def read_only(folder):
    """Remove write bits for the run: the agent cannot change its workspace files."""
    folder = Path(folder)
    saved = []
    for parent, dirs, names in os.walk(folder, followlinks=False):
        for name in names + dirs:
            p = Path(parent) / name
            if not p.is_symlink():
                saved.append((p, p.stat().st_mode))
    saved.append((folder, folder.stat().st_mode))
    try:
        for p, mode in saved:
            os.chmod(p, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        yield
    finally:
        for p, mode in reversed(saved):
            with contextlib.suppress(OSError):
                os.chmod(p, stat.S_IMODE(mode))


def snapshot(folder):
    result = {}
    for parent, dirs, names in os.walk(folder, followlinks=False):
        for name in names:
            p = Path(parent) / name
            with contextlib.suppress(OSError):
                s = p.lstat()
                result[str(p.relative_to(folder))] = (s.st_size, s.st_mtime_ns)
    return result


def _short(value, limit=2000):
    if isinstance(value, dict):
        value = value.get('message') or value.get('type') or json.dumps(value, ensure_ascii=False)
    return str(value or '')[:limit]


class AntigravityStream:
    """Decode agy stream-json. Success requires a terminal result with status SUCCESS."""
    def __init__(self):
        self.text = ''
        self.final = False
        self.failure = None
        self.model = None
        self.tools = set()

    def consume(self, event):
        kind = event.get('event') or event.get('type')
        body = event.get(kind) if isinstance(event.get(kind), dict) else event
        if kind == 'init':
            self.model = body.get('model')
            mode = body.get('permission_mode')
            if mode and mode != 'request-review':
                self.failure = L('Antigravity a démarré avec un mode de permissions non autorisé : ', 'Antigravity started with a permission mode that is not allowed: ') + _short(mode, 100)
                return [(self.failure, 'warning')]
        elif kind == 'step_update':
            entries = []
            if body.get('step_type') == 'agent_response' and isinstance(body.get('text_delta'), str):
                self.text = (self.text + body['text_delta'])[:200000]
            if body.get('step_type') == 'tool':
                info = body.get('tool_info') or {}
                name = str(body.get('tool_name') or info.get('name') or 'outil')[:100]
                key = (body.get('step_index'), name)
                if key not in self.tools:
                    self.tools.add(key)
                    entries.append((L('Outil Antigravity : ', 'Antigravity tool: ') + name, 'tool'))
                if isinstance(info, dict) and info.get('error'):
                    entries.append((L('Une opération Antigravity a échoué : ', 'An Antigravity operation failed: ') + _short(info['error']), 'warning'))
            return entries
        elif kind == 'result':
            self.final = True
            if isinstance(body.get('response'), str) and body['response'].strip():
                self.text = body['response'][:200000]
            if body.get('status') != 'SUCCESS' or body.get('error'):
                self.failure = L('Antigravity n’a pas terminé (', 'Antigravity did not finish (') + _short(body.get('status'), 40) + L(') : ', '): ') + _short(body.get('error') or L('aucune réponse', 'no answer'))
                if re.search(r'auth|sign in|log in', self.failure, re.I):
                    self.failure += L('. Reconnectez le compte Google : lancez « agy » dans le terminal.', '. Reconnect the Google account: run "agy" in the terminal.')
        return []


SOFT_DENY = re.compile(r'(soft-den|denied|not allowed|requires? (approval|permission)|permission)', re.I)


def stderr_notice(line):
    """Permission refusals are reported on stderr while the run continues."""
    line = line.strip()
    if line and SOFT_DENY.search(line):
        return L('Action refusée par les permissions Antigravity : ', 'Action refused by Antigravity permissions: ') + line[:500]
    return None


def _run(binary, args, env, timeout):
    return subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout,
                          env=env, stdin=subprocess.DEVNULL, cwd=env.get('HOME') or None)


def account_status(binary, env):
    """Live check: listing models needs a valid Google session; no model is called."""
    try:
        version = _run(binary, ['--version'], env, 30).stdout.strip()[:40]
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'unavailable', 'detail': L('Antigravity CLI ne répond pas.', 'Antigravity CLI does not respond.')}
    try:
        r = _run(binary, ['models'], env, 60)
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'unavailable', 'version': version, 'detail': L('La liste des modèles n’a pas répondu ; réessayez.', 'The model list did not respond; try again.')}
    output = (r.stdout + '\n' + r.stderr).strip()
    if r.returncode != 0:
        signed_out = re.search(r'sign in|log in|not logged|authentication', output, re.I)
        return {'status': 'disconnected' if signed_out else 'unavailable', 'version': version,
                'detail': L('Compte Google non connecté. Lancez « agy » dans le terminal pour vous connecter.', 'Google account not connected. Run "agy" in the terminal to sign in.') if signed_out else L('Vérification impossible : ', 'Check impossible: ') + output[-300:]}
    models = []
    for line in r.stdout.splitlines():
        line = line.strip(' -*•\t')
        if line and not line.lower().startswith(('fetching', 'available')) and len(line) < 120:
            models.append(line)
    return {'status': 'signed_in', 'version': version, 'models': models[:30],
            'detail': L('Compte Google connecté. Une mission de test confirmera l’exécution.', 'Google account connected. A test mission will confirm it runs.')}
