#!/usr/bin/env bash
# Checks the deliverable tools inside the container: PDF (Pandoc + XeLaTeX), PowerPoint (Pandoc, python-pptx),
# previews (LibreOffice + Poppler). No assistant is called.
# Usage: docker exec atelier bash /app/scripts/docker-check.sh   then   docker cp atelier:/tmp/atelier-test .
set -euo pipefail
rm -rf /tmp/atelier-test && mkdir /tmp/atelier-test && cd /tmp/atelier-test
python3 - <<'PY'
import pypandoc, docx
from pptx import Presentation
md = '## Test Atelier\n\nBonjour, ceci est un essai : é à ç « guillemets ».\n\n## Diapo 2\n\n- point un\n- point deux\n'
pypandoc.convert_text(md, 'pdf', format='md', outputfile='test.pdf', extra_args=['--pdf-engine=xelatex'])
pypandoc.convert_text(md, 'pptx', format='md', outputfile='test.pptx')
print('PDF: OK · PPTX: %d slides' % len(Presentation('test.pptx').slides))
PY
soffice -env:UserInstallation=file:///tmp/atelier-test/lo --headless --convert-to pdf --outdir apercu test.pptx >/dev/null 2>&1
pdftoppm -r 50 -png test.pdf pdf && pdftoppm -r 50 -png apercu/test.pdf pptx
rm -rf lo
ls -1 /tmp/atelier-test
echo 'Previews: OK'
