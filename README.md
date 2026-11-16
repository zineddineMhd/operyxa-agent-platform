# Operyxa — plateforme d'orchestration d'agents IA

Plan. Execute. Verify. Un utilisateur confie une mission d'analyse d'un dépôt GitHub ; des
agents en lecture seule l'exécutent sous contrôle serveur et produisent un rapport dont chaque
constat renvoie à une preuve vérifiée.

État : **MVP lecture seule** — backend (API, worker, agents, passerelle MCP) et interface web (`web/`).
Analyse du cahier des charges et décisions : [docs/analyse-cahier-des-charges.md](docs/analyse-cahier-des-charges.md).

## Architecture

```
POST /api/v1/runs ──> PostgreSQL (runs = file d'attente) ──> worker
                                                              │
   planner (LLM) ─> validation du plan ─> superviseur (déterministe)
                                              │  étapes en parallèle
                                   agents ReAct bornés (repository, security)
                                              │  chaque appel d'outil
                                   passerelle : catalogue > politique > schéma > périmètre
                                              │  > budget > exécution > redaction > audit
                                   serveurs MCP lecture seule (github, analysis)
                                              │
              vérificateur de preuves (déterministe) ─> rapport ─> run_events ─> SSE
```

| Dossier (`backend/src/operyxa/`) | Rôle |
|---|---|
| `api/` | FastAPI : auth JWT, projets, runs, SSE, rapport, preuves |
| `worker/` | Prise en charge des runs, reprise après crash depuis les checkpoints LangGraph |
| `agent/` | Graphe LangGraph, boucle ReAct, validation du plan, vérification des preuves, budgets |
| `gateway/` | Point de passage unique vers les outils : catalogue de confiance, politique, redaction |
| `mcp_servers/` | Serveurs MCP `github` (5 outils GET) et `analysis` (règles statiques) |
| `llm/` | Interface modèle, client Gemini, modèle `demo` déterministe, mesure de consommation |

## Démarrage local

Prérequis : Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL 16.

```bash
cp .env.example .env
```

```bash
cd backend && uv sync && uv run alembic upgrade head
```

```bash
uv run uvicorn operyxa.api.main:app --reload
```

```bash
uv run python -m operyxa.worker
```

Interface web (Node 22), dans un autre terminal :

```bash
cd web && npm install && npm run dev
```

Interface : http://localhost:5173 (les appels `/api` sont relayés vers le port 8000).
Documentation interactive de l'API : http://localhost:8000/docs.
## Modèles

| `LLM_PROVIDER` | Variables | Remarque |
|---|---|---|
| `demo` (défaut) | — | Modèle déterministe hors-ligne, sans clé ni coût |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL` | Palier gratuit : 20 requêtes/jour sur `gemini-3.8-flash`, soit environ un run. Régler `LLM_REQUESTS_PER_MINUTE=5` |
| `openai_compatible` | `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | NVIDIA NIM par défaut ; tout service compatible OpenAI avec appel d'outils. Non encore testé avec une vraie clé |

Dépôts privés : définir `SECRETS_KEY` sur le serveur, puis saisir un jeton GitHub en lecture
seule (Contents + Metadata) à la création du projet. Il est chiffré au repos et jamais renvoyé.

Avec Docker : `docker compose -f infrastructure/compose.yml up --build`.

## Tests

```bash
cd backend && uv run pytest
```

SQLite par défaut ; définir `TEST_DATABASE_URL` pour PostgreSQL. Les cas T-01 à T-10 du cahier des
charges sont couverts, sauf la partie « test échouant » de T-06 (exécution de tests en sandbox,
pas encore développée).

## Interface web

React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query (`web/src`). Écrans : page d'accueil,
connexion, Overview, Projects, Agent Studio (graphe du plan, inspecteur, timeline SSE, budget),
Runs & Traces, MCP Hub, Approvals, Reports (constats avec preuve ouvrable en un clic), Settings,
palette de commandes `Ctrl K`.

Toutes les données affichées viennent de l'API. Ce que le backend ne fait pas encore est indiqué
comme tel : Test Runner « not enabled » (l'Approval Center est fonctionnel mais aucun outil à
risque élevé n'est encore branché), bouton « Cancel run » à la place de « Pause run ».
