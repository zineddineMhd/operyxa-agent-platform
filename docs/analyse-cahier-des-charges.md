# Analyse du cahier des charges v1.0 — backend et agentique

Le cahier des charges est solide sur les principes (evidence-first, contrôle humain, autorisation
hors du modèle, périmètre MVP/V1/V2). Les points ci-dessous sont les manques ou incohérences
relevés, avec la décision retenue dans le code. La partie UX/UI n'est pas traitée ici.

## 1. Incohérences et manques corrigés

| # | Constat dans le cahier des charges | Décision implémentée |
|---|---|---|
| 1 | §10.3 exige des événements SSE persistés et rejouables, mais §9.1 n'a aucune table d'événements. | Table `run_events` (journal append-only) ; son `id` est le curseur `Last-Event-ID`. |
| 2 | « Evidence-first » est un principe, pas un mécanisme : rien ne dit comment une preuve est validée. | Chaque résultat d'outil reçoit un `evidence_id`. Un finding cite `evidence_id` + extrait verbatim. Un vérificateur **déterministe** contrôle que l'extrait existe dans la sortie stockée, et calcule lui-même fichier et ligne (le modèle ne fournit jamais de numéro de ligne). Sinon : `unverified`, exclu des conclusions (T-10). |
| 3 | Le Supervisor est un agent LLM (`RoutingDecision`). Une fois le plan validé, le routage est une fonction pure du graphe de dépendances. | Superviseur **déterministe** : zéro appel LLM, zéro routage halluciné, coût et latence réduits, testable. Le LLM reste là où il apporte de la valeur : plan, étapes ReAct, synthèse. |
| 4 | `PlanStep.agent` inclut `reporting` : le planificateur peut « oublier » le rapport. | Vérification et rapport sont des nœuds fixes du graphe, hors plan. |
| 5 | Le modèle choisit le dépôt dans les arguments (`repo_id`, `path`) : la défense repose sur un contrôle a posteriori. | `owner/repo/ref` sont **injectés par la passerelle** depuis le run et retirés du schéma montré au modèle. Le modèle ne peut pas exprimer « un autre dépôt ». |
| 6 | Aucune révision figée : deux lectures peuvent viser deux commits, les preuves ne sont pas reproductibles. | Le commit analysé est épinglé au démarrage (`runs.commit_sha`) et utilisé pour toutes les lectures. |
| 7 | La redaction ne vise que les logs (§13.1). Or le contenu des fichiers part chez un tiers (Gemini). | Redaction des secrets **avant** stockage et **avant** envoi au LLM ; chemins sensibles (`.env`, `*.pem`, clés…) jamais lus. |
| 8 | Redis + file de jobs non spécifiée (Celery ? arq ?) pour un projet solo de 12 semaines. | PostgreSQL seul pour le MVP : `runs` sert de file (`FOR UPDATE SKIP LOCKED` + bail/heartbeat), `run_events` alimente le SSE. Un service de moins à exploiter ; Redis reviendra si la charge l'exige. |
| 9 | Machine à états sans cause d'arrêt : « budget dépassé », « plan invalide » et « erreur » sont tous `failed`. | Colonne `stop_reason` (`budget_exceeded:tool_calls`, `plan_invalid`, `step_failed`, `repository_unavailable`…) + rapport partiel systématique. |
| 10 | `MAX_RUN_COST_USD` sans table de prix ; §18.1 interdit de figer les tarifs. | Tarifs en configuration (`LLM_PRICING`), coût estimé par appel dans `llm_usage`. Les budgets sont figés sur le run à sa création (`runs.limits`). |
| 11 | `gemini-3.8-flash` et le SDK sont des dépendances mouvantes ; la CI ne peut pas dépendre d'une clé. | Interface `LLMClient` + modèle `demo` déterministe (hors-ligne, coût nul) pour la CI, le dev local et le futur travail frontend. |
| 12 | Modèle de données : `approvals`, `tool_calls`, `findings` sans statut, expiration, durée, ni clé d'idempotence ; pas d'`Idempotency-Key` sur `POST /runs`. | Colonnes ajoutées (voir `db/models.py`) ; `POST /runs` idempotent par en-tête `Idempotency-Key`. |
| 13 | `EventSource` ne peut pas envoyer d'en-tête `Authorization` : le SSE authentifié par JWT ne marche pas tel quel. | Le flux accepte `Last-Event-ID` **et** `?after=` ; le frontend utilisera `fetch` en streaming. Endpoint `events/history` en JSON pour la reprise. |
| 14 | Aucun endpoint pour ouvrir une preuve, alors que c'est la promesse centrale. | `GET /runs/{id}/evidence/{evidence_id}`, `GET /runs/{id}/findings`, `GET /runs/{id}/report.md`. |
| 15 | §10.1 mêle callback OAuth et `installation_id` (GitHub App) : deux modèles d'accès différents. | Recommandation : **GitHub App** (permissions fines en lecture, jetons d'installation courts, rien de durable à stocker). Non implémenté — voir §3. |

## 2. Écarts assumés par rapport au cahier des charges

- **Structure du code** : un seul paquet Python `backend/src/operyxa` (sous-modules `api`, `worker`,
  `agent`, `gateway`, `mcp_servers`, `llm`, `db`) au lieu de `apps/` + `packages/`. Les frontières
  sont les mêmes, sans la complexité d'un monorepo multi-paquets pour un développeur seul.
- **Serveur MCP GitHub maison** (5 outils GET) plutôt qu'un serveur GitHub généraliste : ce qui
  n'est pas exposé ne peut pas être détourné. Un serveur tiers reste branchable (`MCP_GITHUB_URL`),
  filtré par le catalogue de confiance.
- **Tables repoussées en V1** : `mcp_servers`, `tool_policies`, `workflows`, `approvals`,
  `github_connections`, `artifacts`. Au MVP le catalogue d'outils et la politique sont du code
  versionné et testé (`gateway/catalog.py`), pas de la configuration modifiable à chaud.
- **Agents du MVP** : `repository` et `security`. `testing` (F-07) attend le sandbox V1 ; l'option
  `run_tests` est refusée par l'API tant qu'il n'existe pas.

## 3. Ce qui reste à faire côté backend

| Priorité | Sujet | Note |
|---|---|---|
| Haute | Tester avec une vraie clé Gemini | Le client Gemini est écrit et la liaison des outils vérifiée hors-ligne, mais **aucun appel réel** n'a été fait. Prévoir un jeu de cas de référence (qualité du plan, taux de findings vérifiés, coût). |
| Haute | Connexion GitHub App | Aujourd'hui : dépôts publics ou `GITHUB_TOKEN` unique côté serveur. Les dépôts privés multi-utilisateurs exigent l'App + un jeton par installation. |
| Haute | Consentement LLM | Le code d'un dépôt privé est envoyé à Google. À afficher et faire accepter par projet ; vérifier les conditions d'usage des données du palier Gemini utilisé. |
| Moyenne | Refresh tokens, limitation de débit sur `/auth/*` | À décider avec le frontend (cookie httpOnly ou en-tête). |
| Moyenne | Approbations (F-09) | La politique renvoie déjà `approval_required` pour le risque élevé (refus au MVP). Reste : table `approvals` (hash des arguments, expiration), `interrupt()` LangGraph, endpoint de décision. |
| Moyenne | Vulnérabilités connues | `scan_dependencies` contrôle l'hygiène (versions non figées, lockfile). Ajouter une source CVE (OSV) avec contrôle de destination réseau. |
| Basse | Observabilité (OpenTelemetry), rétention/purge, export PDF, LISTEN/NOTIFY pour le SSE | V1. |

## 4. Planning : ajustements proposés

- LangGraph arrive en S8 alors que l'agent est construit en S4–S7 : c'est une réécriture
  programmée. **L'orchestration LangGraph et les checkpoints sont en place dès maintenant.**
- L'UI temps réel n'arrive qu'en S10 : trop tard pour valider le contrat SSE. Le brancher dès
  que le design est fourni (le mode `demo` permet de développer l'UI sans clé ni coût).
- Aucun créneau pour l'évaluation agentique : la prévoir dès que la clé Gemini est disponible,
  car elle conditionne le choix du modèle et les budgets.
