"""Deterministic checks on the final deliverable file: it opens, nothing is left unfinished, slides keep their content in bounds,
the language is the one requested. Standard library and Poppler only; no model is involved."""
import math
import posixpath
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from i18n import L

NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main', 'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'r': 'http://schemas.openxmlformats.org/package/2006/relationships'}
PLACEHOLDER_RE = re.compile(r'\b(?:TODO|FIXME|XXX+)\b|lorem ipsum|\[(?:à compléter|a compléter|à préciser|to be completed|to complete|tbd|insérer[^\]]{0,40}|insert[^\]]{0,40}|placeholder|source\s?\?|citation needed)\]'
                            r'|cliquez pour (?:ajouter|modifier)|click to (?:add|edit)', re.I)
FR = {'le', 'la', 'les', 'des', 'est', 'et', 'une', 'dans', 'pour', 'que', 'qui', 'sur', 'du', 'pas', 'avec', 'sont', 'cette', 'aux', 'par', 'nous', 'leur', 'ces'}
EN = {'the', 'and', 'of', 'to', 'is', 'in', 'that', 'for', 'with', 'are', 'this', 'by', 'be', 'it', 'from', 'which', 'not', 'was', 'have', 'their', 'these'}
EMU_PT = 12700
OVERFLOW = 1.3  # estimated text height over box height before calling it an overflow
MAX_ITEMS = 8
# Order PowerPoint requires inside a shape's properties; any other order makes it ask to repair the file.
SHAPE_ORDER = ('xfrm', 'custGeom', 'prstGeom', 'noFill', 'solidFill', 'gradFill', 'blipFill', 'pattFill', 'grpFill', 'ln', 'effectLst', 'effectDag', 'scene3d', 'sp3d', 'extLst')
# Linux stand-ins for Office fonts, missing on Windows and macOS.
LINUX_FONTS = {'Carlito': 'Calibri', 'Caladea': 'Cambria', 'Liberation Sans': 'Arial', 'Liberation Serif': 'Times New Roman', 'DejaVu Sans': 'Arial'}


def language(text):
    """'fr', 'en', or None when the text is too short or mixed."""
    found = re.findall(r"[a-zà-ÿ]+", text.lower())
    fr, en = sum(w in FR for w in found), sum(w in EN for w in found)
    if fr + en < 40:
        return None
    return 'fr' if fr > 2 * en else 'en' if en > 2 * fr else None


def text_problems(text, lang, where):
    problems = []
    left = sorted({m.group().strip() for m in PLACEHOLDER_RE.finditer(text)})
    if left:
        problems.append(where + L(' : texte provisoire oublié (', ': placeholder text left (') + ', '.join('« ' + x + ' »' for x in left[:5]) + ')')
    found = language(text)
    if lang and found and found != lang:
        problems.append(where + L(' : rédigé en anglais au lieu du français', ': written in English instead of French') if lang == 'fr' else
                        where + L(' : rédigé en français au lieu de l’anglais', ': written in French instead of English'))
    return problems


def missing_parts(z, part):
    """Internal files a part refers to (images, charts) that are absent from the package."""
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, '_rels', name + '.rels')
    if rels not in z.namelist():
        return []
    missing = []
    for rel in ET.fromstring(z.read(rels)).findall('r:Relationship', NS):
        if rel.get('TargetMode') == 'External':
            continue
        target = rel.get('Target', '')
        target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(folder, target))
        if target not in z.namelist():
            missing.append(posixpath.basename(target))
    return missing


def overflowing(sp):
    """Rough estimate: is the text of a text box much taller than the box? Shapes that shrink or grow with their text are ignored."""
    xfrm, body = sp.find('p:spPr/a:xfrm', NS), sp.find('p:txBody', NS)
    if xfrm is None or body is None or xfrm.find('a:ext', NS) is None:
        return False
    pr = body.find('a:bodyPr', NS)
    if pr is not None and (pr.find('a:normAutofit', NS) is not None or pr.find('a:spAutoFit', NS) is not None):
        return False
    ext = xfrm.find('a:ext', NS)
    get = lambda key, default: int(pr.get(key, default)) if pr is not None else default
    width = int(ext.get('cx', 0)) - get('lIns', 91440) - get('rIns', 91440)
    height = int(ext.get('cy', 0)) - get('tIns', 45720) - get('bIns', 45720)
    if width <= 0 or height <= 0:
        return False
    needed, lines = 0, 0
    for para in body.findall('a:p', NS):
        text = ''.join(t.text or '' for t in para.iter('{%s}t' % NS['a']))
        sizes = [int(r.get('sz')) for r in para.iter() if r.tag in ('{%s}rPr' % NS['a'], '{%s}endParaRPr' % NS['a']) and r.get('sz')]
        size = (max(sizes) if sizes else 1800) / 100 * EMU_PT
        count = max(1, math.ceil(len(text) * 0.5 * size / width))
        needed += count * size * 1.2; lines += count
    return lines >= 3 and needed > OVERFLOW * height


def pptx(path, lang):
    problems = []
    with zipfile.ZipFile(path) as z:
        size = ET.fromstring(z.read('ppt/presentation.xml')).find('p:sldSz', NS)
        cx, cy = (int(size.get('cx')), int(size.get('cy'))) if size is not None else (12192000, 6858000)
        slides = sorted((n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)), key=lambda n: int(re.search(r'\d+', posixpath.basename(n)).group()))
        if not slides:
            return [L('PowerPoint : aucune diapositive', 'PowerPoint: no slide')]
        empty, outside, overflow, broken, text, disordered, fonts = [], [], [], [], [], [], {}
        for number, part in enumerate(slides, 1):
            root = ET.fromstring(z.read(part))
            for props in (e for e in root.iter() if e.tag in ('{%s}spPr' % NS['p'], '{%s}grpSpPr' % NS['p'])):
                ranks = [SHAPE_ORDER.index(c.tag.split('}')[-1]) for c in props if c.tag.split('}')[-1] in SHAPE_ORDER]
                if ranks != sorted(ranks):
                    disordered.append(number)
            for e in root.iter():
                if e.get('typeface') in LINUX_FONTS:
                    fonts[e.get('typeface')] = LINUX_FONTS[e.get('typeface')]
            words = ''.join(t.text or '' for t in root.iter('{%s}t' % NS['a'])).strip()
            text.append(words)
            visuals = [e for e in root.iter() if e.tag in ('{%s}pic' % NS['p'], '{%s}graphicFrame' % NS['p'], '{%s}blip' % NS['a'])]
            if not words and not visuals:
                empty.append(number)
            tree = root.find('p:cSld/p:spTree', NS)
            for shape in (list(tree) if tree is not None else []):
                xfrm = next((x for x in (shape.find(q, NS) for q in ('p:spPr/a:xfrm', 'p:xfrm', 'p:grpSpPr/a:xfrm')) if x is not None), None)
                off, ext = (xfrm.find('a:off', NS), xfrm.find('a:ext', NS)) if xfrm is not None else (None, None)
                if off is not None and ext is not None:
                    x, y, w, h = (int(off.get('x', 0)), int(off.get('y', 0)), int(ext.get('cx', 0)), int(ext.get('cy', 0)))
                    if x < -0.01 * cx or y < -0.01 * cy or x + w > 1.01 * cx or y + h > 1.01 * cy:
                        outside.append(number)
                if shape.tag == '{%s}sp' % NS['p'] and overflowing(shape):
                    overflow.append(number)
            broken += ['%d (%s)' % (number, m) for m in missing_parts(z, part)]
    unique = lambda items: ', '.join(str(i) for i in list(dict.fromkeys(items))[:MAX_ITEMS])
    if empty:
        problems.append(L('PowerPoint : diapositive(s) vide(s) n° ', 'PowerPoint: empty slide(s) no. ') + unique(empty))
    if outside:
        problems.append(L('PowerPoint : élément(s) qui dépassent de la diapositive n° ', 'PowerPoint: element(s) extending beyond slide no. ') + unique(outside))
    if overflow:
        problems.append(L('PowerPoint : texte qui déborde probablement de son cadre, diapositive(s) n° ', 'PowerPoint: text probably overflowing its box, slide(s) no. ') + unique(overflow))
    if broken:
        problems.append(L('PowerPoint : image ou graphique manquant, diapositive(s) n° ', 'PowerPoint: missing image or chart, slide(s) no. ') + unique(broken))
    if disordered:
        problems.append(L('PowerPoint : XML de forme dans le mauvais ordre (le remplissage doit suivre la géométrie), PowerPoint demandera une réparation, diapositive(s) n° ',
                          'PowerPoint: shape XML in the wrong order (the fill must follow the geometry), PowerPoint will ask to repair the file, slide(s) no. ') + unique(disordered))
    if fonts:
        problems.append(L('PowerPoint : police(s) absente(s) de Windows et macOS, à remplacer : ', 'PowerPoint: font(s) missing on Windows and macOS, replace: ')
                        + ', '.join('%s → %s' % pair for pair in fonts.items()))
    return problems + text_problems('\n'.join(text), lang, 'PowerPoint')


def docx(path, lang):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        text = '\n'.join(''.join(t.text or '' for t in p.iter('{%s}t' % NS['w'])) for p in root.iter('{%s}p' % NS['w']))
        broken = missing_parts(z, 'word/document.xml')
    problems = []
    if len(text.strip()) < 20 and root.find('.//{%s}drawing' % NS['w']) is None:
        problems.append(L('Word : document vide', 'Word: empty document'))
    if broken:
        problems.append(L('Word : image ou graphique manquant (', 'Word: missing image or chart (') + ', '.join(broken[:MAX_ITEMS]) + ')')
    return problems + text_problems(text, lang, 'Word')


def pdf(path, lang):
    if not shutil.which('pdfinfo') or not shutil.which('pdftotext'):
        return [] if Path(path).read_bytes()[:5] == b'%PDF-' else [L('PDF : le fichier n’est pas un PDF', 'PDF: the file is not a PDF')]
    info = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True, timeout=60)
    pages = re.search(r'^Pages:\s+(\d+)', info.stdout, re.M)
    if info.returncode or not pages or not int(pages.group(1)):
        return [L('PDF : le fichier ne s’ouvre pas', 'PDF: the file does not open')]
    text = subprocess.run(['pdftotext', '-q', '-enc', 'UTF-8', str(path), '-'], capture_output=True, timeout=120).stdout.decode('utf-8', errors='replace')
    if not text.strip():
        return [L('PDF : aucun texte lisible (pages vides ou images seules)', 'PDF: no readable text (blank pages or images only)')]
    return text_problems(text, lang, 'PDF')


def tex(path, lang):
    text = re.sub(r'\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?', ' ', re.sub(r'(?m)%.*$', '', Path(path).read_text(encoding='utf-8', errors='replace')))
    return text_problems(text, lang, 'LaTeX')


CHECKS = {'pptx': pptx, 'docx': docx, 'pdf': pdf, 'tex': tex}


def check(path, lang=None):
    """Problems found in a deliverable file, as short sentences sent back to the final agent."""
    kind = Path(path).suffix.lower().lstrip('.')
    try:
        return CHECKS[kind](path, lang) if kind in CHECKS else []
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return [L('%s : le fichier est endommagé ou ne s’ouvre pas', '%s: the file is damaged or does not open') % kind.upper()]
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return [L('%s : vérification impossible (%s)', '%s: could not be checked (%s)') % (kind.upper(), e)]


def answer(text, lang=None):
    """Same checks on a final answer delivered as text only."""
    return text_problems(text, lang, L('Réponse', 'Answer'))
