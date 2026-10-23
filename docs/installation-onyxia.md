# Installing Atelier on Onyxia (SSP Cloud)

[Onyxia](https://www.onyxia.sh/) provides ready-to-use remote VS Code services (code-server). Atelier installs there with no network setup: its address is derived from the built-in proxy.

## 1. Start a VS Code service

In the Onyxia catalog, launch a **vscode-python** service with a **persistent volume** for `/home/onyxia/work` (enabled by default). Only this folder survives a service restart.

## 2. Install and start

In a VS Code terminal:

```bash
cd /home/onyxia/work
git clone https://github.com/Maxime-Skryma/atelier-agents.git
cd atelier-agents
bash scripts/start.sh
```

The script derives the public address from `VSCODE_PROXY_URI` and prints it at the end, in this form:

```
https://user-<your-id>.user.lab.sspcloud.fr/proxy/8765/
```

Keep the trailing slash. You can close the VS Code tab: the service keeps running as long as the Onyxia service does.

## 3. Connect the assistants

```bash
claude        # then /login
codex login
```

## After an Onyxia service restart

Everything outside `/home/onyxia/work` is wiped: the programs (Claude Code, Codex, LibreOffice, TinyTeX…) and **the assistant logins**. Your projects, missions and deliverables are kept in `atelier-agents/data/`.

Just run again:

```bash
cd /home/onyxia/work/atelier-agents && bash scripts/start.sh
```

The script reinstalls what is missing, then tells you which assistants to log in to again. Atelier deliberately keeps no session.

### Gemini · Antigravity

The `agy` program and its Google session can be kept in `/home/onyxia/work/.atelier-persist/` (`bin/agy` and `gemini/`): `start.sh` then links them back into your home folder at every start.

## Troubleshooting

| Symptom | What to check |
|---|---|
| 404 or 502 error on the link | The service is not running: run `bash scripts/start.sh` again and read `data/service.log`. |
| “Adresse non configurée” | Pass the address explicitly: `bash scripts/start.sh 'https://…/proxy/8765/'`. |
| An assistant shows « À installer » | Run `start.sh` again, or install it by hand, then click « Vérifier les connexions ». |
| PDF fails | XeLaTeX is missing: `quarto install tinytex`, then run the mission again. |
| The link opens VS Code instead of Atelier | code-server checks your VS Code session first. Let the VS Code tab load, then reload the Atelier link. |
