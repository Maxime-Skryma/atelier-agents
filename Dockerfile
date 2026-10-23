# Atelier in a container: the dashboard, its tools and the assistants' CLIs.
# Build: docker compose build   (options: --build-arg WITH_LATEX=0, CLAUDE_CODE_VERSION, CODEX_VERSION)
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm
ARG WITH_LATEX=1
ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Same system tools as scripts/start.sh: LibreOffice + Poppler (previews, visual checks), XeLaTeX (PDF deliverables).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git tini procps \
      libreoffice-impress libreoffice-writer poppler-utils fonts-crosextra-carlito fonts-dejavu-core \
 && if [ "$WITH_LATEX" = 1 ]; then apt-get install -y --no-install-recommends \
      texlive-xetex texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended lmodern; fi \
 && apt-get clean && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/*

# Node.js (for Codex), copied from the official image rather than installed through nvm.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g "@openai/codex@${CODEX_VERSION}" && npm cache clean --force

# Gunicorn, Pandoc (bundled), and the libraries agents use to build PowerPoint and Word files.
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt python-pptx python-docx Pillow matplotlib && rm /tmp/requirements.txt

# Non-root user. Each folder below is a volume in docker-compose.yml: created here so volumes inherit the owner.
RUN useradd --create-home --uid 1000 --shell /bin/bash atelier \
 && mkdir -p /data /opt/agy/bin /home/atelier/.claude /home/atelier/.codex /home/atelier/.gemini \
 && chown atelier:atelier /data /opt/agy /opt/agy/bin /home/atelier/.claude /home/atelier/.codex /home/atelier/.gemini
USER atelier
ENV HOME=/home/atelier CLAUDE_CONFIG_DIR=/home/atelier/.claude CODEX_HOME=/home/atelier/.codex \
    ATELIER_DATA=/data PATH=/opt/agy/bin:/home/atelier/.local/bin:$PATH
# Claude Code: official installer, as in start.sh. No session is created at build time.
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION" && claude --version

# Application code, read-only for the service and the agents.
WORKDIR /app
COPY --chown=root:root . /app
USER root
# House-style Word and PowerPoint templates are generated once here, since /app stays read-only.
RUN chmod 755 /app/scripts/docker-entrypoint.sh \
 && python3 -c "import formats; formats.reference('docx'); formats.reference('pptx')"
USER atelier

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/docker-entrypoint.sh"]
CMD ["serve"]
