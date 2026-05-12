# Rapport Technique Complet: Onboarding v2 + `khatat-lflous`

Date d'analyse: 2026-04-19

## 1. Périmètre et sources

Ce rapport couvre:

- le parcours `/onboarding`
- le parcours `/khatat-lflous`
- les données persistées côté front et backend
- les snapshots dérivés (`draft_objects`)
- la matérialisation backend (`apply`)
- les écrans intermédiaires, overlays et logiques de reprise
- les logiques legacy encore présentes dans le code mais plus utilisées comme chemin principal

Sources principales:

- [onboarding/page.tsx](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/onboarding/page.tsx:1>)
- [khatat-lflous/page.tsx](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/khatat-lflous/page.tsx:1>)
- [KhatatLflousClient.tsx](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/khatat-lflous/KhatatLflousClient.tsx:1>)
- [beta/onboarding-v2/page.tsx](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/beta/onboarding-v2/page.tsx:80>)
- [onboarding_v2_record.py](</Users/mac/Desktop/projet floussy/app/models/onboarding_v2_record.py:15>)
- [onboarding_v2.py](</Users/mac/Desktop/projet floussy/app/schemas/onboarding_v2.py:10>)
- [users.py](</Users/mac/Desktop/projet floussy/app/api/routes/users.py:436>)
- [auth.py](</Users/mac/Desktop/projet floussy/app/api/routes/auth.py:63>)
- [onboarding_v2_apply.py](</Users/mac/Desktop/projet floussy/app/services/onboarding_v2_apply.py:71>)
- [onboarding_distribution_validation.py](</Users/mac/Desktop/projet floussy/app/services/onboarding_distribution_validation.py:1>)
- [distribution.py](</Users/mac/Desktop/projet floussy/app/api/routes/distribution.py:629>)

## 2. Architecture générale

### 2.1 Point clé

`/onboarding` et `/khatat-lflous` ne sont pas deux systèmes séparés. Les deux réutilisent le même composant racine:

- `/onboarding` réexporte directement la page beta onboarding v2.
- `/khatat-lflous` charge dynamiquement le même composant, mais avec `journeyMode="money_plan"`.

Conclusion:

- `onboarding` = phase de collecte et structuration de données
- `khatat-lflous` = phase de planification, arbitrage, enveloppes, distribution et activation
- les deux écrivent dans le même record `onboarding_v2_records`

### 2.2 Sélecteur de mode

Le composant résout le mode de cette manière:

- si le pathname est `/khatat-lflous`, le mode final est forcé à `"money_plan"`
- sinon il prend `"onboarding"` par défaut

Conséquence:

- le même fichier [page.tsx](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/beta/onboarding-v2/page.tsx:10828>) pilote les deux journeys
- seule la sélection des étapes change

## 3. Modèle de persistance

### 3.1 Table SQL

La table [onboarding_v2_records](</Users/mac/Desktop/projet floussy/app/models/onboarding_v2_record.py:15>) contient:

| Champ | Type | Rôle |
| --- | --- | --- |
| `id` | UUID | identifiant du record |
| `user_id` | UUID | propriétaire |
| `flow_version` | string(32) | version de flow, actuellement `v2` |
| `stage` | string(20) | état global: `in_progress`, `review`, `completed` |
| `income_type` | string(40) nullable | copie rapide de `Q0_income_type` |
| `primary_objective` | string(80) nullable | copie rapide de `Q0b_primary_objective` |
| `household_type` | string(80) nullable | copie rapide de `E0_household_type` |
| `payload` | JSONB | snapshot métier complet |
| `created_at` | datetime | création |
| `updated_at` | datetime | dernière mise à jour |

Note de lecture:

- `income_type` et `household_type` peuvent être alimentés dès la phase onboarding
- `primary_objective` dépend surtout de la phase `khatat-lflous`, parce que `Q0b_primary_objective` est aujourd'hui principalement bridgé dans la phase money-plan

### 3.2 Payload JSON enregistré

Le `payload` du record contient jusqu'à 3 blocs:

| Bloc | Provenance | Contenu |
| --- | --- | --- |
| `answers` | front | réponses atomiques et clés de bridge |
| `draft_objects` | front | objets dérivés, projections, résumés, propositions |
| `materialized_state` | backend après `apply` | preuve de matérialisation + résumé d'activation |

Dans `draft_objects`, le front ajoute aussi un snapshot de progression nommé `onboarding_progress_v2`:

- `flow_stage`
- `step_index`
- `current_question_id`
- `is_ready_screen`
- `is_financial_review_screen`
- `is_expense_review_screen`
- `is_rollover_config_screen`
- `is_sweep_setup_screen`
- `is_completion_screen`

### 3.3 Stages utilisés

Le type front [resolveOnboardingRecordStage](</Users/mac/Desktop/projet floussy/floussy-web/src/app/(app)/beta/onboarding-v2/page.tsx:1364>) résout:

- `in_progress`: parcours normal de saisie
- `review`: quand le user est dans un écran de revue/setup final
- `completed`: après `POST /users/me/onboarding-v2-records/latest/apply`

### 3.4 Autosave front

L'autosave passe par:

- `PUT /users/me/onboarding-v2-records/latest`

Le front envoie:

- `flow_version`
- `stage`
- `answers`
- `draft_objects`

L'autosave est déclenché en pratique à partir du snapshot `latestOnboardingRecordSnapshotRef`, avec debounce court après hydratation.

### 3.5 Apply backend

L'activation finale passe par:

- `POST /users/me/onboarding-v2-records/latest/apply`

Le backend:

1. relit `answers` et `draft_objects` du dernier record
2. vérifie l'état du setup distribution
3. matérialise les entités métier
4. ajoute `materialized_state.summary` dans le payload
5. met `record.stage = "completed"`

Important:

- le backend n'interrompt pas l'`apply` si la distribution est invalide
- il ajoute seulement les champs `distribution_setup_valid`, `distribution_setup_source`, `distribution_missing_envelope_names`, etc. au résumé

## 4. Stockage local et mode register guest

Quand le flow tourne en mode guest/register, le front n'écrit pas immédiatement en DB. Il passe aussi par `sessionStorage`:

- `floussy.register.onboarding_v2`
- `floussy.register.onboarding_v2.completed`
- `floussy.register.force_onboarding_v2`
- message inter-frame: `floussy.register.onboarding_v2.complete`

Le payload sauvegardé localement contient aussi:

- `answers`
- `draft_objects`

## 5. Structure des journeys

### 5.1 `onboarding`

Le flow `onboarding` utilise:

- `flowStage = collect_user`
- puis `intro`
- puis `questions`

Les questions money-plan sont exclues du parcours onboarding.

### 5.2 `khatat-lflous`

Le flow `money_plan` démarre directement sur `questions` et ne garde que les 5 étapes suivantes:

1. `F0_financial_summary`
2. `F1_interactive_guidance`
3. `E11_envelope_setup`
4. `E11b_distribution_setup`
5. `E12_smart_settings`

## 6. Détail exhaustif des étapes `onboarding`

## 6.1 Étape 0: `collect_user`

Écran hors `buildQuestions`, piloté par `flowStage`.

Clés enregistrées dans `answers`:

- `R0_profile_photo_url`
- `R1_first_name`
- `R2_last_name`
- `R3_phone_number`
- `R4_birth_date`

Projection dérivée:

- `draft_objects.registration_profile`

Note importante:

- ces données sont bien persistées dans le record
- mais `apply_onboarding_v2_payload` ne les rematérialise pas directement sur `User`
- la mise à jour directe de `User.first_name`, `User.last_name`, `User.phone_number`, `User.birth_date`, `User.profile_photo_url` se fait surtout côté `register` dans [auth.py](</Users/mac/Desktop/projet floussy/app/api/routes/auth.py:367>)

## 6.2 Étape `intro`

Écran d'introduction sans nouvelle clé persistée métier.

Persisté indirectement via:

- `draft_objects.onboarding_progress_v2.flow_stage = "intro"`

## 6.3 Bloc `income`

Étape pivot:

- `Q0_income_type`

### Branche `salaried`

Clés:

- `S1_stability`
- `S2a_salary_amount`
- `S3_frequency`
- `S4_date_mode`
- `S4a_fixed_day`
- `S4b_range`
- `S4c_biweekly_mode`
- `S4c1_biweekly_weekday`
- `S4c2_biweekly_month_dates`
- `S4c3_biweekly_range`
- `S4d_weekly_mode`
- `S4d1_weekly_weekday`
- `S4d2_weekly_weekend_day`
- `S4d3_weekly_range`

### Branche `hirafi`

Clés:

- `H1_income_mode`
- `H2_collection_cycle`
- `H3_income_profile_min`
- `H3_income_profile_weak`
- `H3_income_profile_good`

### Branche `freelancer`

Clés:

- `F1_payment_mode`
- `F1b_collection_cycle`
- `F2_retainer_stability`
- `F3_retainer_day_mode`
- `F3a_retainer_fixed_day`
- `F3b_retainer_range`
- `F5_invoice_delay`
- `F7_min_income`
- `F8_income_variation_weak`
- `F8_income_variation_good`

### Branche `mixed`

Clés:

- `M1_combo`
- `M2_primary_cycle`
- `M2a_monthly_mode`
- `M2b_monthly_fixed_day`
- `M2b_monthly_range`
- `M2c_weekly_mode`
- `M2d_weekly_fixed_day`
- `M2d_weekly_weekend_day`
- `M2d_weekly_range`
- `M2e_project_income_pattern`
- `M2f_project_delay`
- `M3_min_income`

Objets dérivés principaux:

- `income_profile`
- `salary_schedule_profile`
- `salary_amount_effects`
- `salary_notification_beta`
- `cash_flow_timing_v1`
- `sanity_metrics`

## 6.4 Bloc `household`

Question pivot:

- `E0_household_type`

Clés par cas:

- `single`
  - `E0m_single_joke_message` existe comme étape message seulement
  - dérivés automatiques: `E1_adults_count = 1`, `E2_kids_count = 0`
- `couple`
  - `E0c_budget_mode`
  - `E0c_major_expense_owner`
  - `E0c_shared_items`
  - dérivé automatique: `E1_adults_count = 2`
- `family_kids`
  - `E2_kids_count`
  - `E0f_kids_age_groups`
  - `E0f_school_costs`
  - `E0f_school_payment_cycle`
- `extended_family`
  - `E0sh_people_count`
  - `E0sh_split_mode`
  - `E0sh_has_shared_fixed`
  - `E0sh_shared_fixed_items`
  - dérivé automatique: `E2_kids_count = 0`

Toujours présent:

- `E6_support_family`

Objets dérivés:

- `household_profile`
- `categories`
- `sanity_metrics`

## 6.5 Bloc `housing`

Question pivot:

- `E3_housing_status`

### Cas `rent`

- `RNT0_rent_amount`
- `RNT1_rent_includes_costs`
- `RNT1a_rent_included_items`

### Cas `owner_loan`

- `HSN1_loan_monthly_amount`
- `HSN2_loan_remaining_duration`

### Cas `owner_no_loan`

- `HSN3_owner_fixed_items`
- `HSN4_maintenance_saving`
- `HSN4a_maintenance_saving_amount`

### Cas `with_family`

- `HSN5_with_family_contribution`
- `HSN5a_with_family_amount`
- `HSN6_with_family_contribution_types`

Clés cachées / non émises par le questionnaire courant mais encore lues:

- `HSN3_current_cash_available`
- `HSN4_current_cash_available`

Usage de ces clés cachées:

- elles alimentent la lecture du cash disponible immédiat dans `khatat-lflous`
- elles influencent `currentCashAvailableForPlan`, `monthRiskScore`, `guidanceEditableItems`

## 6.6 Bloc `transport`

Question pivot:

- `E4_transport_mode`

### Public

Préfixe `TRP1_`:

- `TRP1_public_monthly_amount`
- `TRP1_public_payment_mode`
- `TRP1_taxi_usage`
- `TRP1_taxi_monthly_amount`

### Car

Préfixe simple: `TR1_`

Préfixes multi-véhicules: `TRV1_`, `TRV2_`, `TRV3_`, `TRV4_`

Clés structurelles:

- `TRV0_has_multiple_vehicles`
- `TRV1_vehicle_count`

Famille de clés voiture par préfixe:

- `${prefix}car_fuel_amount`
- `${prefix}car_insurance_cycle`
- `${prefix}car_insurance_amount`
- `${prefix}car_maintenance_amount`
- `${prefix}car_extra_costs_opt_in`
- `${prefix}car_parking`
- `${prefix}car_parking_amount`
- `${prefix}car_loan`
- `${prefix}car_loan_amount`
- `${prefix}car_inspection`
- `${prefix}car_inspection_cycle`
- `${prefix}car_inspection_amount`
- `${prefix}car_tax`
- `${prefix}car_tax_annual_amount`

### Motorbike

Préfixe simple: `TRM1_`

Préfixes multi-véhicules: `TRV1_` ... `TRV4_`

Famille de clés moto:

- `${prefix}bike_fuel_amount`
- `${prefix}bike_insurance_cycle`
- `${prefix}bike_insurance_amount`
- `${prefix}bike_maintenance_amount`

### Mixed transport

Clés:

- `TRX1_primary_mode`
- `TRX2_total_monthly_amount`
- `TRX3_detail_mode`
- `TRX4_equal_detail_target`

Sous-préfixes détaillés:

- `TRX_P_` pour transport public
- `TRX_C_` pour voiture
- `TRX_B_` pour moto

Objets dérivés:

- `fixed_expenses`
- `cycle_normalized_expenses_v1`
- `expense_priority_layers_v1`

## 6.7 Bloc `fixed_expenses`

Clés:

- `FX0_fixed_now`
- `FX1_fixed_items`
- `FX2_amount_${item}`

Items fixes gérés par l'UI courante:

- `bills`
- `internet_phone`
- `insurance`
- `school`
- `fixed_transport`
- `other`

Transformation importante:

- le builder enrichit aussi automatiquement `fixed_expenses` avec le loyer, le crédit logement, le transport public, le taxi, les coûts voiture, les coûts moto
- donc `fixed_expenses` final n'est pas uniquement un miroir de `FX1_*`

## 6.8 Bloc `debts`

Étapes visibles actuelles:

- `D0_intro_message`
- `E5_has_debt`
- `D1_debt_builder`

Clés structurelles:

- `D1_debt_count`

Famille de clés par dette `i`:

- `D2_debt_name_${i}`
- `D1_debt_type_${i}`
- `D3_debt_remaining_amount_${i}`
- `D4_debt_monthly_payment_${i}`
- `D4_debt_native_amount_${i}`
- `D4_debt_payment_cadence_${i}`
- `D4_debt_payment_native_cadence_${i}`
- `D4_debt_monthly_equivalent_${i}`
- `D4_debt_cycle_equivalent_${i}`
- `D1_debt_payment_style_${i}`
- `D1_debt_status_health_${i}`
- `D1_debt_has_target_date_${i}`
- `D5a_debt_target_date_${i}`

Clés legacy / bridge encore alimentées:

- `D5_debt_target_date_preference`
- `D7_debt_strategy`
- `D7a_focus_debt`
- `D8_debt_extra_monthly`
- `D8a_debt_extra_amount`

Objets dérivés:

- `debts`
- `debt_preferences`
- `debt_summary_v2`
- `debt_plan_v2`

## 6.9 Bloc `goals`

Étapes visibles actuelles:

- `G0_has_goal`
- `G1_goal_builder`

Clés structurelles:

- `G1_goal_count`

Famille de clés par objectif `i`:

- `G1_goal_name_${i}`
- `G1_goal_type_${i}`
- `G1_goal_target_amount_${i}`
- `G1_goal_has_current_amount_${i}`
- `G1_goal_current_amount_${i}`
- `G1_goal_has_date_${i}`
- `G1_goal_target_date_${i}`
- `G1_goal_importance_${i}`

Clés legacy / compatibilité:

- `G1_goal_name`
- `G2_goal_amount`
- `G3_goal_date`
- `G2_goal_intent`
- `G2_goal_urgency_scope`
- `G2_targeted_goal_ids`
- `G2_goal_flexibility`
- `G2_focus_goal_id`
- `G1_goal_monthly_amount_${i}` est encore lu dans certains calculs et résumés

Objets dérivés:

- `goals`
- `goal_preferences`
- `goal_summary_v2`
- `goal_distribution_rules`

## 6.10 Fin du parcours `onboarding`

Le parcours `onboarding` n'affiche pas les étapes money-plan.

Quand toutes les questions onboarding sont complétées:

- `isReadyScreen` passe à `true`
- le front force un `persistOnboardingRecord(..., "review")`
- puis redirige immédiatement vers `/khatat-lflous`

Conséquence produit:

- `onboarding` ne finalise pas l'activation du compte métier
- il sert à préparer les données brutes et dérivées nécessaires à `khatat-lflous`

## 7. Détail exhaustif des étapes `khatat-lflous`

## 7.1 Étape `F0_financial_summary`

Nature:

- hub de lecture
- pas une saisie primaire de nouvelles données

Ce que l'étape affiche:

- résumé du revenu
- résumé des dépenses
- résumé des dettes
- résumé des objectifs
- capacité restante / `remaining`

Sous-écrans liés:

- `financial_review_screen`
- `expense_review_screen`

Effet persistant:

- pas de nouvelle clé dédiée obligatoire
- les modifications faites depuis cette étape réécrivent les clés source existantes: revenu, dépenses, dettes, objectifs

Relation legacy:

- remplace plusieurs lectures séparées plus anciennes
- `debt_summary` existe encore comme renderer dormant mais n'est plus émis dans `buildQuestions`

## 7.2 Sous-écran `financial_review_screen`

Rôle:

- hub de correction rapide

Cibles de navigation:

- revenu
- dépenses
- builder des dettes
- builder des objectifs

Persisté via:

- `draft_objects.onboarding_progress_v2.is_financial_review_screen`

## 7.3 Sous-écran `expense_review_screen`

Rôle:

- modification des lignes de dépense dérivées

Effet:

- réécrit les clés source associées à chaque ligne (`row.answer_key`)
- donc le snapshot dérivé est recalculé en cascade

Persisté via:

- `draft_objects.onboarding_progress_v2.is_expense_review_screen`

## 7.4 Étape `F1_interactive_guidance`

C'est l'étape de planification principale actuelle.

Ce qu'elle fait:

- choisit un scénario de direction
- applique un preset de priorité
- produit les montants planifiés avant la phase enveloppes/distribution

Modes principaux produits:

- `debt_relief_first`
- `stability_first`
- `goal_growth_first`
- `balanced_rebuild`

Clés écrites par `submitInteractiveGuidanceQuestion`:

- `P1_debt_priority`
- `P1_goal_priority`
- `P1_living_priority`
- `Q0b_primary_objective`
- `F1_guidance_mode`
- `F1_guidance_strength_pct`
- `F1_guidance_keep_safety`
- `F1_guidance_keep_flex`
- `F1_guidance_debt_delta`
- `F1_guidance_reserve_delta`
- `F1_guidance_goal_delta`
- `F1_guidance_flex_cut_pct`
- `F1_guidance_planned_debt`
- `F1_guidance_planned_reserve`
- `F1_guidance_planned_flex`
- `F1_guidance_planned_goals`
- `F1_guidance_planned_free`
- `F1_planning_state`
- `F1_lifestyle_fundable`
- `F1_lifestyle_required_selection`
- `F1_lifestyle_base_minimum`
- `F1_lifestyle_base_planned`
- `F1_auto_actions`

Effet métier:

- réconcilie le nouveau moteur `interactive_guidance` avec les anciennes clés `P1_*`
- alimente tous les calculs de posture financière et le futur `contribution_plan_v1`

Legacy remplacé:

- l'ancien écran `priority_profile` existe encore dans le renderer
- le chemin principal n'émet plus `priority_profile`
- `interactive_guidance` le remplace tout en continuant à nourrir `P1_*`

## 7.5 Étape `E11_envelope_setup`

Nature:

- setup structurel des enveloppes
- conservation, exclusion, renommage, ajout d'enveloppes custom

Ce que le user manipule réellement:

- sélection d'enveloppes proposées
- renommage
- rollover par enveloppe
- custom envelopes via dialogue

Clés écrites / bridgées au submit:

- `Q0b_primary_objective`
- `E7_lifestyle`
- `E8_envelope_granularity = "detailed"`
- `E10_keep_suggestions`
- `E11b_distribution_setup = ""`

Important:

- `E7_lifestyle` n'est plus un écran autonome principal
- `E8_envelope_granularity` n'est plus un vrai choix utilisateur dans le flow actuel, il est forcé à `detailed`
- les enveloppes custom modernes vivent surtout dans `draft_objects.envelopes_proposal_v1.custom_envelopes`, pas dans le vieux champ texte `C1_custom_envelopes`

Legacy remplacé:

- `Q0a_envelope_bridge_message`
- `Q0b_primary_objective` comme étape dédiée
- `Q0c_objective_intro_message`
- `E7_lifestyle` comme étape dédiée
- `E8_envelope_granularity` comme étape dédiée
- `E10_keep_suggestions` comme ancienne étape isolée
- `C1_custom_envelopes` texte libre
- `E9_generate_confirm`

## 7.6 Étape `E11b_distribution_setup`

Nature:

- synchronisation avec la logique officielle de distribution
- classification des enveloppes qui doivent recevoir des règles formelles

Clé de complétion:

- `E11b_distribution_setup = "done"`

Validation backend/front:

- appelle l'API de statut distribution
- accepte les statuts:
  - `saved_valid`
  - `applied`
  - `legacy_rules_detected`

Schéma de statut:

- `not_started`
- `draft_opened`
- `saved_valid`
- `applied`
- `invalidated`
- `legacy_rules_detected`

Sources backend possibles:

- `active_config`
- `legacy_rules`
- `none`

Détail important:

- si des `DistributionRule` historiques couvrent déjà les enveloppes, la phase peut être validée avec `legacy_rules_detected`
- la compatibilité legacy est donc explicite

## 7.7 Étape `E12_smart_settings`

Nature:

- écran de consolidation finale

Dans le flow actuel, cette étape fusionne plusieurs anciens écrans séparés:

- rollover
- bootstrap sweep
- résumé final
- CTA d'activation

Clés manipulées:

- `SWP1_last_income_date`
- `SWP2_last_income_amount`

Ce que l'écran contient aujourd'hui:

- réglage du rollover par enveloppe
- résumé des enveloppes
- bootstrap de la première sweep
- résumé final
- bouton `apply`

Ce que l'ancien flow séparait:

- `ready_screen`
- `rollover_config_screen`
- `sweep_setup_screen`
- `completion_screen`

Conclusion:

- les anciens overlays existent encore dans le code et dans `onboarding_progress_v2`
- le flow principal actuel de `money_plan` passe plutôt par un `smart_settings` consolidé

## 8. Relation exacte entre `onboarding` et `khatat-lflous`

### 8.1 Relation fonctionnelle

`onboarding` prépare:

- le profil de revenu
- la structure ménage / logement / transport
- les dépenses fixes
- les dettes
- les objectifs
- les snapshots dérivés de viabilité (`sanity_metrics`, `debt_summary_v2`, `goal_summary_v2`, etc.)

`khatat-lflous` exploite ces données pour:

- calculer l'image financière courante
- choisir une posture de plan
- projeter les contributions par cycle
- stabiliser la structure enveloppes
- brancher la logique officielle de distribution
- configurer le sweep bootstrap
- activer le setup dans les tables métier

### 8.2 Relation de stockage

Les deux pages partagent:

- le même `answers`
- le même `draft_objects`
- le même `onboarding_progress_v2`
- le même record SQL `onboarding_v2_records`

### 8.3 Relation de redirection

Quand `onboarding` se termine:

- le front autosave en `stage = review`
- il push vers `/khatat-lflous`

Donc `khatat-lflous` n'est pas un wizard séparé. C'est la phase 2 du même wizard.

### 8.4 Relation de compatibilité

Le moteur money-plan sait reprendre un ancien record dont `current_question_id` pointe encore vers des étapes legacy:

- `D2_debt_preferences`
- `D3_debt_summary`
- `G2_goal_preferences`
- `Q0a_envelope_bridge_message`
- `Q0c_objective_intro_message`
- `E7_lifestyle`
- `E8_envelope_granularity`
- `E10_keep_suggestions`

Mapping de reprise:

- legacy summary -> `F1_interactive_guidance`
- legacy envelope track -> `E11_envelope_setup`

### 8.5 Matrice de consommation `onboarding` -> `khatat-lflous`

| Bloc onboarding | Principales clés source | Étape khatat consommatrice | Effet |
| --- | --- | --- | --- |
| Profil user | `R0_` à `R4_` | pas central dans khatat | sert surtout au record et au register |
| Income | `Q0_income_type`, `S*`, `H*`, `F*`, `M*` | `F0`, `F1`, `E12` | alimente revenu estimé, cadence sweep, capacité, guidance |
| Household | `E0*`, `E2*`, `E6` | `F0`, `F1`, `E11` | influence catégories, charges, enveloppes proposées |
| Housing | `E3*`, `RNT*`, `HSN*` | `F0`, `F1`, `E11`, `E11b` | crée dépenses fixes, obligations protégées, setup enveloppes |
| Transport | `E4*`, `TR*` | `F0`, `E11`, `E11b` | crée dépenses fixes / variables, enveloppes transport |
| Fixed expenses | `FX*` | `F0`, `E11`, `E11b` | nourrit `fixed_expenses`, `cycle_normalized_expenses_v1`, règles de distribution |
| Debts | `E5_has_debt`, `D*` | `F0`, `F1`, `E11`, `apply` | image dette, posture dette, extra dette, cibles distribution |
| Goals | `G0_has_goal`, `G1*` | `F0`, `F1`, `E11`, `apply` | image objectifs, priorité, goal envelopes, goal distribution rules |
| Rien de plus après onboarding | snapshots dérivés déjà construits | `F0` à `E12` | khatat ne redemande pas le brut; il arbitre et active |

## 9. Catalogue technique des `answers`

## 9.1 Réponses directement collectées par le questionnaire courant

Familles actuellement émises par le flow principal:

- `R0_` à `R4_`
- `Q0_income_type`
- `S*`, `H*`, `F*`, `M*`
- `E0*`, `E2*`, `E6`
- `E3*`, `RNT*`, `HSN*`
- `E4*`, `TR*`
- `FX*`
- `E5_has_debt`, `D1_*`, `D2_debt_name_*`, `D3_*`, `D4_*`, `D5a_*`
- `G0_has_goal`, `G1_*`
- `SWP1_last_income_date`, `SWP2_last_income_amount`

## 9.2 Réponses encore utilisées mais non émises comme étapes principales

Clés de posture et compatibilité:

- `P1_debt_priority`
- `P1_goal_priority`
- `P1_living_priority`
- `P1_priority_profile`

Clés guidance:

- `F1_guidance_mode`
- `F1_guidance_strength_pct`
- `F1_guidance_keep_safety`
- `F1_guidance_keep_flex`
- `F1_guidance_debt_delta`
- `F1_guidance_reserve_delta`
- `F1_guidance_goal_delta`
- `F1_guidance_flex_cut_pct`
- `F1_guidance_planned_debt`
- `F1_guidance_planned_reserve`
- `F1_guidance_planned_flex`
- `F1_guidance_planned_goals`
- `F1_guidance_planned_free`
- `F1_planning_state`
- `F1_lifestyle_fundable`
- `F1_lifestyle_required_selection`
- `F1_lifestyle_base_minimum`
- `F1_lifestyle_base_planned`
- `F1_auto_actions`

Clés envelope legacy / bridge:

- `Q0a_envelope_bridge_message`
- `Q0b_primary_objective`
- `Q0c_objective_intro_message`
- `E7_lifestyle`
- `E8_envelope_granularity`
- `E9_generate_confirm`
- `E10_keep_suggestions`
- `E11b_distribution_setup`
- `C1_custom_envelopes`

Clés debt legacy:

- `D2_pressure_feeling`
- `D2_preferred_strategy`
- `D2_focus_debt_id`
- `D2_comfort_level`
- `D2_target_date_preference`
- `D2_targeted_debt_ids`
- `D5_debt_target_date_preference`
- `D7_debt_strategy`
- `D7a_focus_debt`
- `D8_debt_extra_monthly`
- `D8a_debt_extra_amount`

Clés goal legacy:

- `G2_goal_amount`
- `G3_goal_date`
- `G2_goal_intent`
- `G2_goal_urgency_scope`
- `G2_targeted_goal_ids`
- `G2_goal_flexibility`
- `G2_focus_goal_id`
- `G1_goal_monthly_amount_${i}`

Clés notifications / dormant:

- `N0_notifications`
- `N1_types`
- `N2_time`
- `N3_weekly_day`

Clés cash disponible / cachées:

- `HSN3_current_cash_available`
- `HSN4_current_cash_available`

Clés auto-dérivées:

- `E1_adults_count`
- `D4_debt_payment_native_cadence_${i}`
- `D4_debt_monthly_equivalent_${i}`
- `D4_debt_cycle_equivalent_${i}`

## 10. Catalogue technique des `draft_objects`

Le `buildDraftObjects` courant retourne:

- `registration_profile`
- `household_profile`
- `envelope_suggestions`
- `income_profile`
- `distribution_rules`
- `goals`
- `goal_preferences`
- `goal_summary_v2`
- `sinking_funds`
- `notification_prefs`
- `envelopes`
- `categories`
- `mappings`
- `fixed_expenses`
- `debts`
- `debt_preferences`
- `debt_summary_v2`
- `debt_plan_v2`
- `financial_priority_profile`
- `distribution_posture_v1`
- `debt_posture`
- `goal_posture`
- `living_margin_level`
- `reserve_policy`
- `sinking_fund_policy`
- `cash_flow_timing_v1`
- `expense_priority_layers_v1`
- `cycle_normalized_expenses_v1`
- `recommended_distribution_rules_v1`
- `reserve_plan_v1`
- `contribution_plan_v1`
- `goal_distribution_rules`
- `user_settings`
- `finish_decision`
- `envelope_lookup`
- `sanity_metrics`
- `salary_schedule_profile`
- `salary_amount_effects`
- `salary_notification_beta`
- `sweep_bootstrap_v1`
- `objective_effects`
- `guidance_direction_v1`
- `envelopes_proposal_v1`
- `onboarding_progress_v2` est ajouté au niveau `recordDraftObjects`

### 10.1 Objets de projection métier les plus importants

`financial_priority_profile`

- synthèse priorité dette / objectif / vie
- mode recommandé
- posture dette / objectif
- politique réserve
- niveau réserve

`distribution_posture_v1`

- snapshot canonique de la posture budgétaire choisie
- utilisé aussi dans `apply`

`contribution_plan_v1`

- montants discrétionnaires par cycle
- mode sélectionné
- allocations par mode

`envelopes_proposal_v1`

- `selected_envelopes`
- `excluded_envelopes`
- `edited_names`
- `edited_rollover`
- `custom_envelopes`
- `candidates`
- `signals`
- `intelligence_v2`
- `explain_v2`

`sanity_metrics`

- lecture de viabilité globale revenu / charges / remaining

`cash_flow_timing_v1`

- `cycle_label`
- `cadence_label`
- `interval_days`
- `last_income_date`
- `last_income_amount`
- `expected_income_per_cycle`
- `fixed_total_per_cycle`
- `debt_minimum_total_per_cycle`
- `protected_expenses_per_cycle`
- `planned_obligations_per_cycle`

## 11. Matérialisation backend réelle

Le backend `apply_onboarding_v2_payload` matérialise réellement:

- `Envelope`
- `Category`
- `CategoryEnvelopeMap`
- `Goal`
- `DistributionRule`
- `User.sweep_interval_days`
- `User.next_sweep_date`
- `User.auto_distribution_enabled`

### 11.1 Envelopes

Création / mise à jour à partir de:

- `envelopes_proposal_v1.selected_envelopes`
- goals
- sinking funds

Cas particuliers:

- `Cash` et `Épargne/Epargnes` sont traités comme spéciaux
- les enveloppes goal sont marquées `is_goal = True`

### 11.2 Categories et mappings

Créées à partir de:

- `categories`
- `mappings`
- catégories custom portées par certaines enveloppes sélectionnées

### 11.3 Goals et sinking funds

Sources:

- `goals`
- `sinking_funds`

Les sinking funds deviennent aussi des lignes `Goal`, mais avec:

- `goal_type = "sinking_fund"`

### 11.4 Distribution rules

Règle importante:

- si l'utilisateur a déjà au moins une `DistributionRule`, l'apply n'en recrée pas de nouvelles
- sinon le backend crée des règles à partir de:
  - `cycle_normalized_expenses_v1`
  - `reserve_plan_v1`
  - `debt_plan_v2`
  - `goals`
  - `sinking_funds`

### 11.5 Ce qui n'est pas matérialisé directement

Ces blocs restent dans le record comme snapshot documentaire / analytique:

- `sanity_metrics`
- `salary_amount_effects`
- `salary_notification_beta`
- `objective_effects`
- `guidance_direction_v1`
- la majorité des flags explicatifs de `envelopes_proposal_v1`

### 11.6 Materialized state

Après apply, le backend ajoute:

- `payload.materialized_state.applied = true`
- `payload.materialized_state.applied_at`
- `payload.materialized_state.summary`

Le `summary` expose notamment:

- compteurs d'enveloppes / catégories / mappings / goals / sinking funds créés ou mis à jour
- posture de distribution
- profile financier
- reserve plan
- cash flow timing
- validité setup distribution
- source de setup distribution
- enveloppes non couvertes ou manquantes

## 12. Logiques legacy, dormantes ou remplacées

## 12.1 Kinds encore dans le renderer mais plus émis par le flow principal

Les kinds suivants existent encore dans le composant:

- `debt_preferences`
- `debt_summary`
- `goal_preferences`
- `priority_profile`
- `debt_plan_preview`

État actuel:

- le renderer existe
- `isQuestionAnswered(...)` les connaît encore
- mais `buildQuestions(...)` ne les émet plus dans le chemin principal

## 12.2 Étapes remplacées par des hubs plus récents

Remplacements majeurs:

- `debt_preferences` + `debt_summary` + `debt_plan_preview` -> remplacés par `D1_debt_builder`, `F0_financial_summary`, `F1_interactive_guidance`
- `goal_preferences` -> absorbé par `G1_goal_builder` + `F1_interactive_guidance`
- `priority_profile` -> remplacé par `interactive_guidance`
- `Q0b_primary_objective`, `E7_lifestyle`, `E8_envelope_granularity`, `E10_keep_suggestions` comme étapes isolées -> remplacées par `E11_envelope_setup`
- `ready_screen`, `rollover_config_screen`, `sweep_setup_screen`, `completion_screen` -> en grande partie absorbés dans `E12_smart_settings`

## 12.3 Bridges legacy encore actifs

Le code maintient plusieurs bridges:

- `completeFirstGoalAnswers(...)` copie le premier objectif builder vers `G1_goal_name`, `G2_goal_amount`, `G3_goal_date`
- `submitDebtPreferencesQuestion(...)` alimente `D7_debt_strategy` et `D7a_focus_debt`
- `submitInteractiveGuidanceQuestion(...)` alimente `P1_*` même si le user n'a pas vu l'ancien écran `priority_profile`
- `submitEnvelopeSetupQuestion(...)` réalimente `Q0b_primary_objective`, `E7_lifestyle`, `E8_envelope_granularity`, `E10_keep_suggestions`
- le statut distribution accepte `legacy_rules_detected`

## 12.4 Écrans legacy encore persistables via `onboarding_progress_v2`

Même si le flow principal actuel ne s'appuie plus fortement dessus, `onboarding_progress_v2` garde:

- `is_ready_screen`
- `is_financial_review_screen`
- `is_expense_review_screen`
- `is_rollover_config_screen`
- `is_sweep_setup_screen`
- `is_completion_screen`

Conséquence:

- le système de reprise sait encore restaurer des états intermédiaires d'anciens flows

## 13. Synthèse finale

Lecture produit correcte:

- `onboarding` collecte et normalise la matière première
- `khatat-lflous` transforme cette matière première en posture, enveloppes, distribution et bootstrap de cycle
- les deux partagent la même persistance et les mêmes snapshots dérivés

Lecture technique correcte:

- le système actuel est un wizard unifié à deux phases
- il conserve une forte couche de compatibilité legacy
- beaucoup de clés historiques sont encore vivantes même quand l'écran d'origine n'est plus dans le parcours principal
- `draft_objects` est le vrai centre métier du flow
- `apply` matérialise seulement une partie du snapshot, le reste reste comme trace analytique et de reprise
