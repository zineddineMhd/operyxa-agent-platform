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
Avec `LLM_PROVIDER=demo` (défaut) tout fonctionne sans clé ni coût ; passer à
`LLM_PROVIDER=gemini` + `GEMINI_API_KEY` pour le vrai modèle.

Avec Docker : `docker compose -f infrastructure/compose.yml up --build`.

## Tests

```bash
cd backend && uv run pytest
```

SQLite par défaut ; définir `TEST_DATABASE_URL` pour PostgreSQL. Les cas T-01 à T-10 du cahier des
charges sont couverts, sauf T-07 (approbations, V1) et la partie « test échouant » de T-06
(exécution de tests en sandbox, V1).

## Interface web

React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query (`web/src`). Écrans : page d'accueil,
connexion, Overview, Projects, Agent Studio (graphe du plan, inspecteur, timeline SSE, budget),
Runs & Traces, MCP Hub, Approvals, Reports (constats avec preuve ouvrable en un clic), Settings,
palette de commandes `Ctrl K`.

Toutes les données affichées viennent de l'API. Ce que le backend ne fait pas encore est indiqué
comme tel : file d'approbations vide, Test Runner « not enabled », bouton « Cancel run » à la place
de « Pause run ».
