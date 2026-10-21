"""Deliverable formats: guidance for the final agent and Pandoc conversion of its answer."""
import datetime as dt
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from i18n import L

# id -> (label, extension)
FORMATS = {'pdf': ('PDF', 'pdf'), 'docx': ('Word', 'docx'), 'pptx': ('PowerPoint', 'pptx'), 'tex': ('LaTeX', 'tex')}
SLIDES_MARK = '<!-- DIAPOSITIVES -->'
# House style applied to Word and PowerPoint through Pandoc reference documents, built from Pandoc's own defaults.
TEMPLATES = Path(__file__).resolve().parent / 'modeles'
STYLE_VERSION = '1'
NAVY, BLUE, LIGHT = '16324F', '1F6FB2', 'F4F6F9'
PALETTE = {'dk2': NAVY, 'lt2': LIGHT, 'accent1': BLUE, 'accent2': 'E07B39', 'accent3': '2A9D8F', 'accent4': '7B61C9', 'accent5': 'E9B949', 'accent6': '6C7A89', 'hlink': BLUE}
FONTS = ('Calibri Light', 'Calibri')
MONTHS = {'fr': ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'],
          'en': ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']}


def theme(xml):
    for k, v in PALETTE.items():
        xml = re.sub(r'<a:%s>.*?</a:%s>' % (k, k), '<a:%s><a:srgbClr val="%s"/></a:%s>' % (k, v, k), xml, count=1, flags=re.S)
    xml = re.sub(r'(<a:majorFont><a:latin typeface=")[^"]*', r'\g<1>' + FONTS[0], xml, count=1)
    return re.sub(r'(<a:minorFont><a:latin typeface=")[^"]*', r'\g<1>' + FONTS[1], xml, count=1)


def shape(id, name, x, y, w, h, color):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr userDrawn="1"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="%s"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>') % (id, name, x, y, w, h, color)


def after_group(xml, shapes):
    # Decorations go first in the shape tree, behind the placeholders.
    return xml.replace('</p:grpSpPr>', '</p:grpSpPr>' + shapes, 1)


def dark_layout(xml, accent, line):
    """Full-colour background, white left-aligned text, master decorations hidden."""
    xml = xml.replace('preserve="1">', 'preserve="1" showMasterSp="0">', 1)
    xml = re.sub(r'(<p:cSld name="[^"]*">)', r'\1<p:bg><p:bgPr><a:solidFill><a:srgbClr val="%s"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>' % NAVY, xml, count=1)
    xml = after_group(xml, shape(90, 'Bande', 0, 0, 228600, 5143500, accent) + shape(91, 'Filet', 685800, line, 1371600, 45720, accent))
    xml = xml.replace('cap="all"/>', 'cap="all"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:defRPr>')
    xml = re.sub(r'<a:schemeClr val="tx1">(<a:tint val="75000"/>)?</a:schemeClr>', '<a:srgbClr val="D6E2EE"/>', xml)
    return xml.replace('<a:bodyPr/><a:lstStyle/>', '<a:bodyPr anchor="b"/><a:lstStyle><a:lvl1pPr algn="l"><a:defRPr sz="4000" b="1"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:defRPr></a:lvl1pPr></a:lstStyle>', 1).replace('algn="ctr"', 'algn="l"')


def pptx_part(name, xml):
    if name == 'ppt/theme/theme1.xml':
        return theme(xml)
    if name == 'ppt/slideMasters/slideMaster1.xml':
        xml = after_group(xml, shape(90, 'Bande', 0, 0, 91440, 5143500, NAVY) + shape(91, 'Filet', 457200, 1063229, 731520, 36576, BLUE) + shape(92, 'Pied', 0, 5097780, 9144000, 45720, LIGHT))
        title = re.search(r'<p:titleStyle>.*?</p:titleStyle>', xml, re.S).group(0)
        xml = xml.replace(title, title.replace('algn="ctr"', 'algn="l"').replace('<a:defRPr sz="3300" kern="1200"><a:solidFill><a:schemeClr val="tx1"/>', '<a:defRPr sz="2400" b="1" kern="1200"><a:solidFill><a:schemeClr val="tx2"/>'))
        body = re.search(r'<p:bodyStyle>.*?</p:bodyStyle>', xml, re.S).group(0)
        new = re.sub(r'(<a:defRPr sz=")(\d+)', lambda m: m.group(1) + str(int(m.group(2)) * 5 // 6 // 100 * 100), body)
        new = new.replace('<a:buFont typeface="Arial"/>', '<a:buClr><a:schemeClr val="accent1"/></a:buClr><a:buFont typeface="Arial"/>').replace('<a:spcPct val="20000"/>', '<a:spcPts val="900"/>')
        return xml.replace(body, new.replace('<a:schemeClr val="tx1"/>', '<a:srgbClr val="2B3A4A"/>'))
    if name == 'ppt/slideLayouts/slideLayout1.xml':
        return dark_layout(xml, BLUE, 2788920)
    if name == 'ppt/slideLayouts/slideLayout3.xml':
        return dark_layout(xml, PALETTE['accent2'], 3214000)
    return xml


def docx_part(name, xml):
    if name == 'word/theme/theme1.xml':
        return theme(xml)
    if name == 'word/styles.xml':
        title = re.search(r'<w:style [^>]*w:styleId="Title".*?</w:style>', xml, re.S).group(0)
        return xml.replace(title, title.replace('<w:jc w:val="center" />', '<w:jc w:val="left" /><w:pBdr><w:bottom w:val="single" w:sz="12" w:space="6" w:color="%s" /></w:pBdr>' % BLUE)
                           .replace('<w:kern w:val="28" />', '<w:kern w:val="28" /><w:b /><w:color w:val="%s" />' % NAVY))
    return xml


def reference(kind):
    """Path of the styled reference.<kind>, rebuilt when the house style changes."""
    target = TEMPLATES / ('reference-%s.%s' % (STYLE_VERSION, kind))
    if target.exists():
        return target
    TEMPLATES.mkdir(exist_ok=True)
    default = subprocess.run([pandoc(), '--print-default-data-file', 'reference.' + kind], capture_output=True, check=True, timeout=60).stdout
    patch = pptx_part if kind == 'pptx' else docx_part
    tmp = target.with_suffix('.%d.tmp' % os.getpid() + str(id(default)))
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as out, zipfile.ZipFile(io.BytesIO(default)) as src:
        for item in src.infolist():
            data = src.read(item)
            if item.filename.endswith('.xml'):
                data = patch(item.filename, data.decode('utf-8')).encode('utf-8')
            out.writestr(item, data)
    tmp.replace(target)
    return target


def pandoc():
    try:
        import pypandoc
        path = pypandoc.get_pandoc_path()
        if path and Path(path).exists():
            return path
    except (ImportError, OSError):
        pass
    return shutil.which('pandoc')


def clean(formats):
    if not isinstance(formats, list) or not all(isinstance(f, str) for f in formats):
        raise ValueError('Formats de rendu invalides.')
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ValueError('Format de rendu inconnu : ' + ', '.join(unknown))
    return [f for f in FORMATS if f in formats]


CONTENT_ONLY = ('\n\nFINAL DELIVERABLE: the last agent of the team alone will produce the final file (PowerPoint, PDF, Word or LaTeX), so that its design stays consistent. '
                'Create no file in these formats and do not work on the look. Instead, supply all the material, complete and precise: '
                'the fully written content, figures with their sources, data tables, a detailed outline (proposed split into slides or sections, with the message of each) '
                'and ideas for visuals or charts, together with the data needed to build them.')


def previews(source, folder, pages=8, timeout=120):
    """Write apercu-NN.png page images of a document into folder. Silent when LibreOffice or Poppler is missing."""
    source, folder = Path(source), Path(folder)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = source
            if source.suffix.lower() in ('.png', '.jpg', '.jpeg', '.tex'):
                return 0
            if source.suffix.lower() != '.pdf':
                if not shutil.which('soffice'):
                    return 0
                subprocess.run(['soffice', '-env:UserInstallation=file://' + tmp + '/profil', '--headless', '--convert-to', 'pdf', '--outdir', tmp, str(source)], capture_output=True, timeout=timeout)
                pdf = Path(tmp) / (source.stem + '.pdf')
            if not pdf.exists() or not shutil.which('pdftoppm'):
                return 0
            subprocess.run(['pdftoppm', '-r', '60', '-l', str(pages), '-png', str(pdf), str(folder / 'apercu')], capture_output=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return 0
    return len(list(folder.glob('apercu-*.png')))


def guidance(formats, references=()):
    names = ', '.join(FORMATS[f][0] for f in formats)
    text = ('\n\nOUTPUT FORMATS: your final answer will be converted automatically to ' + names + '. '
            'Write it as the final document itself, with no introductory sentence and no comment on your work. '
            'Use standard Markdown: sections with #, subsections with ##, lists, Markdown tables, bold and italics; no HTML. '
            'Write every formula in LaTeX: $...$ inline, $$...$$ for display formulas (aligned, cases, pmatrix allowed inside). '
            'Never write a formula in Unicode characters or plain text.')
    if 'tex' in formats or 'pdf' in formats:
        text += ' For typographic quality: rigorous, consistent mathematical notation, \\tag{} to number the equations you refer to, units and symbols defined where they first appear.'
    if 'docx' in formats:
        text += ' Formulas will become editable Word equations: keep them in standard LaTeX, with no custom macros.'
    if 'pptx' in formats:
        text += ('\nPOWERPOINT: ' + ('after the document, ' if len(formats) > 1 else '') + 'write a line containing exactly ' + SLIDES_MARK +
                 ' then the presentation version, the way a consultant would, with as many slides as the content needs (a full study usually takes 15 to 30). '
                 'A # heading creates a coloured section slide (2 to 4 sections). A ## heading creates a slide: write that heading as the key message ("Sales doubled in two years", not "Sales"). '
                 'Each slide carries one message in short bullets, no long paragraph; put the detail and the reasoning in the speaker notes rather than dropping it. Vary the layout: a Markdown table to compare, '
                 'two columns with :::: {.columns} then ::: {.column width="50%"} … ::: to contrast two ideas, a slide with a single key figure as a large title. '
                 'Add speaker notes under each slide in a ::: notes … ::: block. Only the key formulas.')
    text += ('\nYOU ALONE ARE RESPONSIBLE FOR THE DESIGN: the other agents supplied the content; you alone design the final file, with a single visual identity '
             '(a palette of 2 or 3 colours, one or two fonts, consistent margins and grid). Never paste files or styles from different sources as they are.'
             '\nIF YOU CAN RUN CODE (terminal), build the final file yourself in your current directory (for example with python-pptx or python-docx), '
             'with a professional finish worthy of a consulting firm: clear typographic hierarchy, visuals and charts built from the data, no overloaded slide. '
             'Then CHECK THE RENDERING: convert it to images (soffice -env:UserInstallation=file:///tmp/lo-$$ --headless --convert-to pdf, then pdftoppm -r 50 -png), '
             'look at every page and fix overflows, overlaps, text that is too small, weak contrast and empty pages; repeat until the result is flawless. '
             'Then delete these check images. Otherwise, just write the Markdown described above: Atelier will format it.')
    if references:
        text += ('\nEXPECTED DESIGN LEVEL: the user provided reference examples in references/ (' + ', '.join(references[:12]) + (', …' if len(references) > 12 else '') + '). '
                 'The apercu-NN.png images show their pages. Look at them before starting and aim at least for this level: layout, palette, typography, density, use of visuals. '
                 'Draw on their style, never on their content.')
    return text


def split(markdown):
    document, mark, slides = markdown.partition(SLIDES_MARK)
    return document.strip(), (slides.strip() if mark else markdown.strip())


def render(markdown, title, formats, output, timeout=180, examples=None, lang='fr'):
    """Convert the final answer into output/livrable.<ext>. Returns [(format, error or None)]."""
    binary = pandoc()
    if not binary:
        return [(f, L('Pandoc n’est pas installé.', 'Pandoc is not installed.')) for f in formats]
    output = Path(output)
    document, slides = split(markdown)
    (output / 'livrable.md').write_text(document, encoding='utf-8')
    if 'pptx' in formats:
        (output / 'diapositives.md').write_text(slides, encoding='utf-8')
    # TeX may only read and write inside the output folder.
    env = dict(os.environ, openin_any='p', openout_any='p')
    results = []
    for f in formats:
        source = 'diapositives.md' if f == 'pptx' else 'livrable.md'
        today = dt.date.today()
        cmd = [binary, source, '--from', 'markdown', '--standalone', '--metadata', 'title=' + title, '--metadata', 'lang=' + lang, '--metadata', 'date=' + ('%d %s %d' % (today.day, MONTHS['fr'][today.month - 1], today.year) if lang == 'fr' else '%s %d, %d' % (MONTHS['en'][today.month - 1], today.day, today.year)), '-o', 'livrable.' + FORMATS[f][1]]
        if f == 'pdf':
            cmd += ['--pdf-engine', 'xelatex', '-V', 'geometry:margin=2.5cm', '-V', 'colorlinks=true', '-V', 'linkcolor=[HTML]{%s}' % BLUE, '-V', 'urlcolor=[HTML]{%s}' % BLUE, '-V', 'toccolor=[HTML]{%s}' % NAVY]
        else:
            cmd += ['--sandbox']
        if f == 'pptx':
            cmd += ['--slide-level', '2']
        try:
            styles = []
            if f in ('pptx', 'docx'):
                # The user's own example first (its theme and layouts), then the house style, then Pandoc's default.
                styles = [x for x in (examples or {}).get(f, []) if Path(x).suffix.lower() == '.' + f]
                try:
                    styles.append(reference(f))
                except (subprocess.SubprocessError, OSError, AttributeError, KeyError):
                    pass
            for style in styles + [None]:
                r = subprocess.run(cmd + (['--reference-doc', str(style)] if style else []), cwd=output, env=env, capture_output=True, text=True, timeout=timeout)
                if r.returncode == 0:
                    break
            error = None if r.returncode == 0 else ' / '.join((r.stderr.strip().splitlines() or [L('erreur inconnue', 'unknown error')])[-6:])[-600:]
        except subprocess.TimeoutExpired:
            error = L('conversion trop longue', 'conversion took too long')
        except OSError as e:
            error = str(e)
        results.append((f, error))
    return results
