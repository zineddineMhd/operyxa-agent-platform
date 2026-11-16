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
| 15 | §10.1 mêle callback OAuth et `installation_id` (GitHub App) : deux modèles d'accès différents. | Pour l'instant : jeton GitHub en lecture seule par projet, chiffré au repos, injecté par la passerelle et invisible du modèle. Cible : **GitHub App** — voir §3 bis. |

## 2. Écarts assumés par rapport au cahier des charges

- **Structure du code** : un seul paquet Python `backend/src/operyxa` (sous-modules `api`, `worker`,
  `agent`, `gateway`, `mcp_servers`, `llm`, `db`) au lieu de `apps/` + `packages/`. Les frontières
  sont les mêmes, sans la complexité d'un monorepo multi-paquets pour un développeur seul.
- **Serveur MCP GitHub maison** (5 outils GET) plutôt qu'un serveur GitHub généraliste : ce qui
  n'est pas exposé ne peut pas être détourné. Un serveur tiers reste branchable (`MCP_GITHUB_URL`),
  filtré par le catalogue de confiance.
- **Tables repoussées en V1** : `mcp_servers`, `tool_policies`, `workflows`,
  `github_connections`, `artifacts`. Au MVP le catalogue d'outils et la politique sont du code
  versionné et testé (`gateway/catalog.py`), pas de la configuration modifiable à chaud.
- **Agents du MVP** : `repository` et `security`. `testing` (F-07) attend le sandbox V1 ; l'option
  `run_tests` est refusée par l'API tant qu'il n'existe pas.

## 3. Enseignements du premier run réel avec Gemini

Le modèle `demo` ne pouvait pas révéler ces défauts ; tous sont corrigés et couverts par un test.

| Constat réel | Correction |
|---|---|
| HTTP 503 « high demand » fréquents, et un appel resté bloqué sans réponse | Réessais avec temporisation, délai maximal par appel, modèle de secours (`GEMINI_FALLBACK_MODEL`), motif d'arrêt `llm_unavailable` |
| Schéma d'outil refusé (400) : la propriété `title` des findings était supprimée par erreur | Seules les métadonnées JSON-schema sont retirées ; les erreurs 4xx ne sont plus réessayées |
| Palier gratuit : **20 requêtes par jour** et 5 par minute sur `gemini-3.8-flash` | Limiteur de débit par modèle (`LLM_REQUESTS_PER_MINUTE`) ; sur quota épuisé, bascule immédiate vers le modèle de secours |
| Plan rejeté parce que le modèle écrit des identifiants longs ou avec majuscules | Les identifiants sont normalisés côté serveur au lieu de rejeter le plan |
| Le modèle a tenté deux fois de lire `.env` | Refus `SENSITIVE_PATH_DENIED` : la politique a tenu sans dépendre du prompt |

Un run complet sur le dépôt de test consomme environ 16 appels LLM et 27 000 tokens en entrée :
le budget par défaut de 20 appels est juste, et le palier gratuit de `gemini-3.8-flash` ne permet
qu'environ un run par jour.

Même run sur NVIDIA (`nvidia/nemotron-3-ultra-550b-a55b`) : 20 appels LLM, 65 000 tokens en
entrée, 25 constats tous vérifiés mais largement redondants, quatre étapes planifiées dont trois
pour le même agent. D'où la déduplication déterministe ajoutée ensuite (`agent/dedup.py`) : deux
constats dont la preuve principale désigne le même fichier et la même ligne sont fusionnés, le
plus sévère est conservé. Sur les 7 modèles NVIDIA essayés, 3 gèrent l'appel d'outils dans les
délais ; les 4 autres n'ont pas répondu en 90 secondes.

Un second run a épuisé le budget avant l'étape sécurité : le budget d'appels est désormais
réparti entre les étapes du plan (`turns_per_step`) et le plan est limité à 4 étapes. Résultat du
run suivant, avec osv.dev réel : terminé en 14 appels, 8 constats vérifiés, 2 doublons fusionnés,
3 affirmations non vérifiées écartées des conclusions (dont un « secret en dur » de sévérité
critique dont la citation ne correspondait pas à la sortie d'outil — à examiner : la redaction
des secrets modifie le texte que le modèle doit citer).

## 3 bis. Ce qui reste à faire côté backend

| Priorité | Sujet | Note |
|---|---|---|
| Haute | Sandbox d'exécution de tests (F-07) | Le circuit d'approbation est en place et testé avec un faux exécuteur. Il manque le serveur MCP `test_runner` réel : conteneur sans réseau, sans secrets, utilisateur non privilégié, profils autorisés. À développer et tester sur une machine avec Docker. |
| Haute | Consentement LLM | Le code d'un dépôt privé est envoyé au fournisseur du modèle. À afficher et faire accepter par projet ; vérifier les conditions d'usage des données du palier utilisé (le palier gratuit Gemini n'offre pas les mêmes garanties que le payant). |
| Moyenne | GitHub App | Les dépôts privés passent aujourd'hui par un jeton par projet, chiffré au repos. Une GitHub App éviterait de stocker un jeton durable. |
| Moyenne | Refresh tokens, limitation de débit sur `/auth/*` | À décider selon le mode de déploiement. |
| Moyenne | Vulnérabilités : lire les lockfiles | `scan_vulnerabilities` interroge osv.dev à partir des manifestes. Pour une plage (`^1.2`, `>=2.0`) il teste la version minimale autorisée ; lire le lockfile donnerait la version réellement installée. |
| Moyenne | Jeu de cas d'évaluation | Pour comparer modèles et réglages de budget, et mesurer les fusions à tort de la déduplication. |
| Moyenne | Citations de texte masqué | Quand un secret est masqué, le modèle cite parfois le texte d'origine supposé : le constat part en « non vérifié ». Le guider dans le prompt, ou accepter la citation de la ligne masquée. |
| Basse | Observabilité (OpenTelemetry), rétention/purge, export PDF, LISTEN/NOTIFY pour le SSE | V1. |

## 4. Planning : ajustements proposés

- LangGraph arrive en S8 alors que l'agent est construit en S4–S7 : c'est une réécriture
  programmée. **L'orchestration LangGraph et les checkpoints sont en place dès maintenant.**
- L'UI temps réel n'arrive qu'en S10 : trop tard pour valider le contrat SSE. Le brancher dès
  que le design est fourni (le mode `demo` permet de développer l'UI sans clé ni coût).
- Aucun créneau pour l'évaluation agentique : la prévoir dès que la clé Gemini est disponible,
  car elle conditionne le choix du modèle et les budgets.
