"""Deterministic checks on an agent answer: cited sources must exist and contain the figures and quotations attributed to them. No model is involved."""
import concurrent.futures
import html
import ipaddress
import re
import shutil
import socket
import subprocess
import tempfile
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

# Figures and quotations must appear in the source cited on the same line.
MAX_BYTES = 5_000_000
MIN_TEXT = 1500  # below this, the page is probably a cookie wall or built by JavaScript: no conclusion
NUMBER_RE = re.compile(r'(?<![\w.,])\d{1,3}(?:[   ,.]\d{3})+(?:[.,]\d+)?(?![\w])|(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])')
UNIT_RE = re.compile(r'\s?(%|‰|€|\$|£|k€|M€|Md€|mds?\b|millions?\b|milliards?\b|billions?\b|thousand\b|trillions?\b)', re.I)
QUOTE_RE = re.compile(r'«\s*([^»\n]{20,400}?)\s*»|“([^”\n]{20,400})”|"([^"\n]{20,400})"')
NOISE_RE = re.compile(r'\[[^\]\n]*\]\(https?://[^)\s]*\)|\bpp?\.\s?\d+(?:[-–]\d+)?|n°\s?\d+|§\s?\d+|\[\^?[^\]\s]{1,20}\]|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}\b|^\s*(?:[-*+]|\d+[.)])\s')
NOTE_RE = re.compile(r'^\[\^([^\]]+)\]:\s*(.*)$')
REF_RE = re.compile(r'^\s*(?:[-*]\s*)?\[(\d{1,3})\]:?\s+(.*)$')
CITE_RE = re.compile(r'\[\^([^\]]+)\]|\[(\d{1,3})\](?!\()')
SOURCES_HEADING_RE = re.compile(r'^#{1,6}\s*(?:sources?|références|references|bibliograph\w*|notes)\b', re.I)
MAX_CLAIMS = 15


def page_text(body, ctype='', charset=None):
    """Readable text of a downloaded page or PDF."""
    if 'pdf' in (ctype or '') or body[:5] == b'%PDF-':
        return pdf_text(body)
    text = body.decode(charset or 'utf-8', errors='replace')
    text = re.sub(r'(?is)<(script|style|noscript|svg)\b.*?</\1>', ' ', text)
    return html.unescape(re.sub(r'<[^>]+>', ' ', text))


def pdf_text(data):
    if not shutil.which('pdftotext'):
        return ''
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        f.write(data); f.flush()
        try:
            return subprocess.run(['pdftotext', '-q', '-enc', 'UTF-8', f.name, '-'], capture_output=True, timeout=60).stdout.decode('utf-8', errors='replace')
        except (subprocess.SubprocessError, OSError):
            return ''


def file_text(path):
    """Text of a project document: plain text, PDF, or anything Pandoc reads (Word, PowerPoint, HTML…)."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix in ('.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.tex'):
            return path.read_text(encoding='utf-8', errors='replace')
        if suffix in ('.html', '.htm'):
            return page_text(path.read_bytes())
        if suffix == '.pdf':
            return pdf_text(path.read_bytes())
        if suffix in ('.docx', '.pptx', '.odt', '.epub', '.rtf'):
            import formats
            binary = formats.pandoc()
            if binary:
                return subprocess.run([binary, str(path), '--to', 'plain', '--wrap', 'none'], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return ''


def digits(token):
    return re.sub(r'\D', '', token)


def source_numbers(text):
    """Every figure of a source, as digit strings, with integer parts (a rounded figure still matches)."""
    found = set()
    for token in NUMBER_RE.findall(text):
        found.add(digits(token))
        whole = re.match(r'\d{1,3}(?:[   ,.]\d{3})+|\d+', token).group()
        found.add(digits(whole))
        for sep in ',.':  # "1,5" as well as "1.234": both readings
            if sep in token:
                found.add(digits(token.split(sep)[0]))
    return found


def words(text):
    return re.sub(r'\W+', ' ', text.lower().replace('’', "'")).strip()


def claim_numbers(line):
    """Figures worth checking in a line: with a unit, decimals or at least 10, never years, dates or list numbers."""
    line = URL_RE.sub(' ', NOISE_RE.sub(' ', line))
    result = []
    for m in NUMBER_RE.finditer(line):
        token = m.group()
        unit = UNIT_RE.match(line, m.end())
        plain = re.fullmatch(r'\d+', token)
        if plain and len(token) == 4 and 1800 <= int(token) <= 2100 and not unit:
            continue
        if plain and int(token) < 10 and not unit:
            continue
        result.append((token + (unit.group() if unit else ''), token, bool(unit)))
    return result


def claims(text, sources):
    """Figures and quotations missing from the source cited on the same line, and figures with no source.
    sources maps a URL or an inputs/ path to its text. Returns (problems, warnings)."""
    lines = text.splitlines()
    notes, refs = {}, {}
    for line in lines:
        m = NOTE_RE.match(line.strip())
        if m:
            notes[m.group(1)] = m.group(2)
        m = REF_RE.match(line)
        if m and (URL_RE.search(m.group(2)) or FILE_RE.search(m.group(2))):
            refs[m.group(1)] = m.group(2)
    known = {key: (val, source_numbers(val), words(val)) for key, val in sources.items() if len(val) >= MIN_TEXT}
    problems, unsourced, skip = [], [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            skip = bool(SOURCES_HEADING_RE.match(stripped)); continue
        if skip or not stripped or NOTE_RE.match(stripped) or REF_RE.match(line):
            continue
        cited = stripped + ' ' + ' '.join(notes.get(n, '') or refs.get(r, '') for n, r in CITE_RE.findall(stripped))
        keys = [u.rstrip(TRAILING) for u in URL_RE.findall(cited)] + [f.rstrip(TRAILING) for f in FILE_RE.findall(URL_RE.sub(' ', cited))]
        figures = claim_numbers(stripped)
        if not keys:
            if not stripped.startswith('|') and any(unit for _, _, unit in figures):
                unsourced.append(stripped[:90])
            continue
        texts = [known[k] for k in keys if k in known]
        if not texts:
            continue  # the source could not be read: nothing to conclude
        label = ', '.join(k for k in keys if k in known)[:120]
        for shown, token, _ in figures:
            wanted = {digits(token)}
            if re.search(r'[.,]0+$', token):
                wanted.add(digits(re.split(r'[.,]', token)[0]))
            if not any(wanted & numbers for _, numbers, _ in texts):
                problems.append(L('chiffre « %s » introuvable dans %s — « %s »', 'figure "%s" not found in %s — "%s"') % (shown, label, stripped[:90]))
        for groups in QUOTE_RE.findall(stripped):
            quote = words(next(g for g in groups if g))
            terms = [w for w in quote.split() if len(w) > 3]
            if len(quote.split()) < 5 or not terms:
                continue
            for _, _, body in texts:
                if quote in body:
                    break
            else:
                # Mostly the same words, but not verbatim: the quotation was altered. Otherwise it is a translation.
                if any(sum(w in body for w in terms) >= 0.6 * len(terms) for _, _, body in texts):
                    problems.append(L('citation inexacte, absente telle quelle de %s — « %s »', 'inaccurate quotation, not found verbatim in %s — "%s"') % (label, quote[:90]))
    warnings = [L('%d chiffre(s) sans source citée sur la même ligne : ', '%d figure(s) with no source on the same line: ') % len(unsourced) + ' ; '.join('« ' + u + ' »' for u in unsourced[:5])] if unsourced else []
    if len(problems) > MAX_CLAIMS:
        problems = problems[:MAX_CLAIMS] + [L('… et %d autre(s)', '… and %d more') % (len(problems) - MAX_CLAIMS)]
    return problems, warnings

GUIDANCE = ('\n\nSOURCES: for every source you cite, give a full link (https://…), a DOI (10.xxxx/…) or an arXiv identifier, '
            'or the path of a file in inputs/. Atelier will automatically check that each one exists: never cite a source you are not certain of.'
            '\nFIGURES AND QUOTATIONS: cite the source of a figure or a quotation on the same line or in the same paragraph (inline link, [^note] or [n] reference), '
            'pointing to the exact page that contains it. Copy figures and quotations exactly as written in the source: Atelier checks that they appear in it.'
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


def fetch(url, timeout=12, keep=None):
    """'ok', 'missing', 'blocked' or 'unknown', with a short reason. With keep (a dict), the text of a page found is stored in keep[url]."""
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
        with urllib.request.urlopen(request, timeout=timeout) as r:
            if keep is not None:
                try:
                    keep[url] = page_text(r.read(MAX_BYTES), r.headers.get_content_type(), r.headers.get_content_charset())
                except (OSError, ValueError):
                    pass
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
    pages = {}
    jobs = [(u, lambda u: fetch(u, keep=pages), u) for u in urls] + [('DOI ' + d, check_doi, d) for d in dois] + [('arXiv:' + a, check_arxiv, a) for a in arxiv]
    problems, unverified, blocked = [], [], []
    root = Path(workspace).resolve()
    for f in files[:LIMIT]:
        target = (root / f).resolve()
        if root not in target.parents or not target.exists():
            problems.append(f + L(' : fichier absent du dossier de la mission', ': file missing from the mission folder'))
        elif target.is_file():
            pages[f] = file_text(target)
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
    wrong, unsourced = claims(text, pages)
    return problems + wrong, unverified + unsourced, blocked
