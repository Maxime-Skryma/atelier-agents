"""Deterministic checks on an agent answer: cited sources must exist. No model is involved."""
import concurrent.futures
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from i18n import L

URL_RE = re.compile(r'https?://[^\s<>"\'`\])»]+')
DOI_RE = re.compile(r'\b10\.\d{4,9}/[^\s<>"\'`\])»]+')
ARXIV_RE = re.compile(r'\barXiv:\s?(\d{4}\.\d{4,5})(?:v\d+)?', re.I)
FILE_RE = re.compile(r'\b(?:inputs|contributions)/[^\s<>"\'`\])»,;]+')
TRAILING = '.,;:!?*_'
LIMIT = 40
# A plain browser identity: many sites refuse unknown user agents without any real protection.
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
           'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8'}

# Only subscriptions/logins and real anti-bot protections (challenge pages, captchas) count as refused access.
PAYWALL = (401, 402)
CHALLENGE_HEADERS = ('cf-mitigated', 'x-datadome', 'x-px-block', 'x-amzn-waf-action')
CHALLENGE_RE = re.compile(rb'captcha|challenge-platform|cf-chl|just a moment|datadome|perimeterx|verify you are human', re.I)
DOWNLOADS = 'Articles to download'
# Older answers used the French heading.
DOWNLOADS_RE = re.compile(r'^#{1,6}\s*(?:' + DOWNLOADS + r'|Articles à télécharger)\b.*$', re.I | re.M)

GUIDANCE = ('\n\nSOURCES: for every source you cite, give a full link (https://…), a DOI (10.xxxx/…) or an arXiv identifier, '
            'or the path of a file in inputs/. Atelier will automatically check that each one exists: never cite a source you are not certain of.'
            '\nWEB ACCESS: use is personal and non-commercial. A robots.txt file, a "no automated access" notice or terms of use hostile to bots are not blockers: '
            'read the page anyway and use its content. Never bypass, however, a subscription, a mandatory login or a real anti-bot protection (captcha, verification page).'
            '\nINACCESSIBLE ARTICLES: only if an article that looks relevant is truly inaccessible to you (subscription or mandatory login, captcha or anti-bot verification page), '
            'do not summarise it and do not guess its content. End your answer with a "## ' + DOWNLOADS + '" section with one line per article: '
            '"- Title — link or DOI — why it would be useful". The user will download it and add it to the project documents.')


def downloads(text):
    """Split the agent's 'Articles to download' section out of its answer: (answer without it, [lines])."""
    match = DOWNLOADS_RE.search(text)
    if not match:
        return text, []
    rest = text[match.end():]
    end = re.search(r'^#{1,6}\s|^<!--', rest, re.M)
    section, after = (rest[:end.start()], rest[end.start():]) if end else (rest, '')
    items = [l.strip()[1:].strip() for l in section.splitlines() if l.strip()[:1] in '-*•' and l.strip()[1:].strip()]
    return (text[:match.start()].rstrip() + ('\n\n' + after.lstrip() if after.strip() else '')).strip(), items


def references(text):
    """Return (urls, dois, arxiv ids, files) cited in text, deduplicated, in order."""
    urls, dois = [], []
    for u in URL_RE.findall(text):
        u = u.rstrip(TRAILING)
        host = urllib.parse.urlsplit(u).hostname or ''
        # doi.org links are checked through the DOI registry: publishers often refuse automated visits.
        if host in ('doi.org', 'dx.doi.org') and DOI_RE.search(u):
            dois.append(DOI_RE.search(u).group().rstrip(TRAILING))
        else:
            urls.append(u)
    for d in DOI_RE.findall(URL_RE.sub(' ', text)):
        dois.append(d.rstrip(TRAILING))
    arxiv = ARXIV_RE.findall(text)
    files = [f.rstrip(TRAILING) for f in FILE_RE.findall(URL_RE.sub(' ', text))]
    unique = lambda items: list(dict.fromkeys(items))
    return unique(urls), unique(dois), unique(arxiv), unique(files)


def public(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return None
    return all(ipaddress.ip_address(i[4][0].split('%')[0]).is_global for i in infos)


def fetch(url, timeout=12):
    """'ok', 'missing' or 'unknown', with a short French reason."""
    host = urllib.parse.urlsplit(url).hostname
    if not host:
        return 'missing', L('adresse invalide', 'invalid address')
    reachable = public(host)
    if reachable is None:
        return 'missing', L('domaine inexistant', 'domain does not exist')
    if not reachable:
        return 'unknown', L('adresse locale non vérifiée', 'local address not checked')
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return 'ok', ''
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return 'missing', L('page introuvable (%d)', 'page not found (%d)') % e.code
        if e.code in PAYWALL:
            return 'blocked', L('abonnement ou connexion requis (%d)', 'subscription or login required (%d)') % e.code
        try:
            body = e.read(20000)
        except OSError:
            body = b''
        if any(h in e.headers for h in CHALLENGE_HEADERS) or CHALLENGE_RE.search(body or b''):
            return 'blocked', L('protection anti-robot (%d)', 'anti-bot protection (%d)') % e.code
        return 'unknown', L('le site refuse la vérification automatique (%d)', 'the site refuses automated checks (%d)') % e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 'unknown', L('site injoignable (%s)', 'site unreachable (%s)') % getattr(e, 'reason', e)


def check_doi(doi):
    status, reason = fetch('https://doi.org/api/handles/' + urllib.parse.quote(doi, safe='/'))
    return status, L('DOI inexistant', 'DOI does not exist') if status == 'missing' else reason


def check_arxiv(ident):
    status, reason = fetch('https://arxiv.org/abs/' + ident)
    return status, L('article arXiv inexistant', 'arXiv article does not exist') if status == 'missing' else reason


def check(text, workspace):
    """Return (problems, unverified, blocked): missing sources, sources that could not be checked, and existing pages that refuse access."""
    urls, dois, arxiv, files = references(text)
    jobs = [(u, fetch, u) for u in urls] + [('DOI ' + d, check_doi, d) for d in dois] + [('arXiv:' + a, check_arxiv, a) for a in arxiv]
    problems, unverified, blocked = [], [], []
    root = Path(workspace).resolve()
    for f in files[:LIMIT]:
        target = (root / f).resolve()
        if root not in target.parents or not target.exists():
            problems.append(f + L(' : fichier absent du dossier de la mission', ': file missing from the mission folder'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: (job[0],) + job[1](job[2]), jobs[:LIMIT]))
    for label, status, reason in results:
        if status == 'missing':
            problems.append(label + L(' : ', ': ') + reason)
        elif status == 'blocked':
            blocked.append(label + ' — ' + reason)
        elif status == 'unknown':
            unverified.append(label + L(' : ', ': ') + reason)
    if len(jobs) > LIMIT:
        unverified.append(L('%d autres sources non vérifiées (limite de %d)', '%d more sources not checked (limit of %d)') % (len(jobs) - LIMIT, LIMIT))
    return problems, unverified, blocked
