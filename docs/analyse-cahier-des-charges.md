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
- **Agents** : `repository` et `security` ; `testing` (F-07) seulement quand le run demande
  `run_tests` et que le serveur active le test runner (sinon l'API refuse l'option).
- **Pas de commandes libres pour les tests** : le cahier des charges parle d'exécuter des tests ;
  le modèle ne choisit qu'un profil défini côté serveur (image et commandes figées), jamais une
  ligne de commande. Un profil manquant s'ajoute dans `mcp_servers/sandbox.py`, avec revue.

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
| Moyenne | Test runner : déploiement | F-07 est en place (serveur `test_runner`, agent `testing`, approbation, tests d'évasion sur Docker réel). Restent : image Docker du serveur et service `compose`, essai avec un vrai modèle, et gVisor à valider sur l'hôte cible. L'installation des dépendances n'a pas été essayée avec réseau dans l'environnement de développement (pas d'accès aux registres depuis les conteneurs). |
| Moyenne | Test runner : installation sans réseau | Avec `TEST_RUNNER_INSTALL_NETWORK=true`, le code d'installation du dépôt tourne avec accès réseau (sans secrets). Cible : un miroir de paquets interne comme seule destination autorisée. |
| Haute | Consentement LLM | Le code d'un dépôt privé est envoyé au fournisseur du modèle. À afficher et faire accepter par projet ; vérifier les conditions d'usage des données du palier utilisé (le palier gratuit Gemini n'offre pas les mêmes garanties que le payant). |
| Moyenne | GitHub App | Les dépôts privés passent aujourd'hui par un jeton par projet, chiffré au repos. Une GitHub App éviterait de stocker un jeton durable. |
| Moyenne | Refresh tokens | À décider selon le mode de déploiement. La limitation de débit sur `/auth/*` est en place (mémoire d'un processus API ; un stockage partagé sera nécessaire avec plusieurs instances). |
| Moyenne | Vulnérabilités : lire les lockfiles | `scan_vulnerabilities` interroge osv.dev à partir des manifestes. Pour une plage (`^1.2`, `>=2.0`) il teste la version minimale autorisée ; lire le lockfile donnerait la version réellement installée. |
| Moyenne | Jeu de cas d'évaluation | Pour comparer modèles et réglages de budget, et mesurer les fusions à tort de la déduplication. |
| Basse | Observabilité (OpenTelemetry), rétention/purge, LISTEN/NOTIFY pour le SSE | V1. |
| Moyenne | Équipes : invitations | Un membre doit déjà avoir un compte ; pas d'e-mail d'invitation. |
| Moyenne | PR : essai réel | Le flux est testé contre un faux GitHub ; à essayer sur un dépôt de test avec un jeton d'écriture dédié. |

## 3 ter. Défauts des essais réels corrigés (26/11)

| Défaut | Correction |
|---|---|
| La redaction tournait sur le JSON encodé : `KEY = "valeur"` devenait `KEY = \"valeur\"` et échappait aux motifs ; le secret partait au modèle et en base. | Redaction feuille par feuille sur le JSON décodé (`redact_tool_output`), valeurs entre guillemets masquées en entier (espaces compris), nouveaux formats de clés (`nvapi-`, `sk-`, Stripe). Arguments, événements, résumés et constats sont aussi masqués. |
| Une clé privée multi-ligne devenait une seule ligne : les numéros de ligne suivants étaient faux. | Le bloc masqué garde BEGIN/END et son nombre de lignes. |
| La troncature à 40 000 caractères coupait le JSON : la preuve perdait chemin et lignes. | Troncature structurée (`gateway/truncation.py`) : les plus grandes chaînes sont raccourcies sur des fins de ligne, les listes perdent leur queue ; le JSON reste valide. |
| Le modèle citait l'original supposé d'un secret masqué : constat « non vérifié ». | Le prompt demande de citer le texte masqué ; le vérificateur compare aussi les deux côtés masqués. La citation stockée ne contient jamais le secret. |

## 3 quater. Exigences V1/V2 livrées (26/11)

- **F-10 MCP Hub et politiques** : `GET /mcp/servers?probe=true` (santé, outils disponibles, outils ignorés hors catalogue) ; politiques par projet (`/projects/{id}/tool-policies`) : désactiver un outil ou exiger une approbation. Une surcharge ne peut que durcir le catalogue ; elle est figée sur le run (`runs.policy`).
- **F-11 Exports et métriques** : `report.pdf`, `report.json`, `report.md?download=true` ; `GET /runs/{id}/metrics` (durée, taux de vérification, erreurs d'outils, budget consommé) et indicateurs ajoutés à `/analytics/overview`.
- **F-12 PR avec confirmation** : agent `remediation` et serveur MCP `changes` qui ne fait que proposer un diff ; refus des workflows CI, des fichiers à secrets et des chemins sensibles. Une personne prépare la PR (instantané + `content_hash`), puis la confirme en citant ce hash ; le serveur crée alors une branche neuve, un commit et une PR brouillon avec un jeton d'écriture distinct. Aucun agent n'a d'outil d'écriture.
- **F-13 Workflows et équipes** : 5 workflows intégrés (analyse, audit sécurité, architecture, tests, correction) et workflows enregistrés (qui ne peuvent que restreindre les agents) ; équipes avec rôles viewer / member / admin / owner appliqués à toutes les routes.

## 3 quinquies. Mise en route (26/11)

- `start.cmd` / `start.ps1` (Windows) et `start.sh` : une commande génère les secrets dans `.env` et lance toute la pile ; l'interface est servie par nginx dans `compose` (port 8080, SSE sans mise en tampon).
- Démarrage refusé hors `local` si `JWT_SECRET_KEY` est la valeur d'exemple ou trop courte, ou si `SECRETS_KEY` est un texte d'exemple.
- Worker et `python -m operyxa.api` utilisables sous Windows (boucle d'événements compatible psycopg, signaux).
- Reste pour un usage en ligne : HTTPS et nom de domaine, sauvegardes de la base, consentement LLM par projet.

## 4. Planning : ajustements proposés

- LangGraph arrive en S8 alors que l'agent est construit en S4–S7 : c'est une réécriture
  programmée. **L'orchestration LangGraph et les checkpoints sont en place dès maintenant.**
- L'UI temps réel n'arrive qu'en S10 : trop tard pour valider le contrat SSE. Le brancher dès
  que le design est fourni (le mode `demo` permet de développer l'UI sans clé ni coût).
- Aucun créneau pour l'évaluation agentique : la prévoir dès que la clé Gemini est disponible,
  car elle conditionne le choix du modèle et les budgets.
