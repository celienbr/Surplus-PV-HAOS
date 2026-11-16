# Surplus PV — Home Assistant OS 1.0.0-alpha.19

Surplus PV utilise les mesures Home Assistant, les prévisions et le planificateur
confort. Le GP8600 peut commander le chauffe-eau après configuration.
La puissance affichée est une demande, non une mesure physique.

## Mise à jour depuis alpha.18

1. Dans Paramètres → Système → Sauvegardes, sauvegarder l'application et ses
   données ; conserver une copie de la sauvegarde hors du Raspberry Pi.
2. Dans Paramètres → Applications → Boutique, rechercher les mises à jour,
   ouvrir l'application Surplus PV existante et choisir Mettre à jour. Ne pas
   désinstaller l'application : son slug reste `surplus_pv_diagnostic`.
3. Vérifier la version 1.0.0-alpha.19, les réglages de confort et les historiques.
   Les bases SQLite et les préférences restent dans /data.

Dans Pilotage, la consigne AUTO utilisateur est distincte du plafond thermique
`boiler_max_temp_c` des options Supervisor. Le choix Prévision réutilise le
calcul Forecast.Solar ; si les données manquent ou sont périmées, le repli
indiqué à l'écran s'applique. Le PV prévu reste brut et ne garantit pas de
surplus réel. L'appoint réseau reste limité au minimum de confort autorisé.
Les préférences de confort dans l'interface sont verrouillées si
`use_addon_settings` est activé ; les options Supervisor font alors autorité.
Aucun réglage d'interface ne peut relever le plafond thermique.

## Configuration réelle

Renseigner l'adresse I²C et `boiler_temperature_entity` de la sonde d'eau.
Pour AUTO réel, saisir les couples
mesurés `[W, mA]` strictement croissants dans `power_curve_points`, couvrant
la plage de puissance configurée, puis confirmer par `power_curve_confirmed`.
Sans courbe validée, AUTO réel reste indisponible. Un accusé I²C ne mesure ni le
courant ni la puissance, et zéro demandé ne prouve pas une coupure physique.

Le diagnostic `api/status` expose `control_timing` : dates des rapports HA,
nombre d'observations validées, intervalle entre les deux dernières, et temps
monotones internes de réception, calcul et écriture. Ces temps internes ne sont
pas des dates civiles ; comparer leurs différences. `telemetry_queue_dropped`
compte les enregistrements perdus en cas de file d'historique saturée. Ces
indications ne mesurent pas la puissance physique ni la cadence du compteur.

Pour démarrer le watchdog avec le service, définir `heartbeat_autostart: true`,
`heartbeat_period_s`, `heartbeat_width_s` et `heartbeat_task_timeout_s`. La période
et la durée d'impulsion sont nulles par défaut : les renseigner avec les valeurs
établies sur l'installation. Le timeout est au moins deux fois l'intervalle
d'échantillonnage. L'impulsion doit durer au moins 50 ms et le repos au moins
100 ms. La coordination avec le temporisateur physique doit être vérifiée séparément.
Une configuration incomplète empêche
le démarrage automatique et produit une erreur dans le journal. Aucun ancien
ordre de chauffe n'est repris au démarrage. AUTO est demandé dès le démarrage ;
il reste inhibé tant que le watchdog, la sonde, la calibration ou les mesures
nécessaires ne sont pas valides. Une température supérieure à 83 °C est invalide.

En ARRÊT, la chauffe n'est pas demandée. En AUTO, la demande dépend des mesures
et du plan de confort. En MANUEL, elle dépend du réglage utilisateur et de la
protection thermique. Une mesure absente ou périmée inhibe les commandes qui en
ont besoin ; le watchdog continue si les tâches critiques progressent. La perte
d'impulsions signale une défaillance du logiciel ; le relais Omron ne connaît
ni les prévisions ni l'heure de confort. S1 et K2 ne disposent pas de retour
mesuré dans l'application ; S2 est le secours matériel. Les protections thermiques
indépendantes restent nécessaires.

Les préférences Alpha.18 sont conservées dans le fichier v3 sous `legacy` ;
un premier enregistrement B3 conserve aussi une sauvegarde `.pre-b3.backup`
lorsqu'un fichier existait. Sans plafond configuré, la commande réelle
reste inhibée. Les réglages MANUEL mémorisés ne sont jamais réappliqués
automatiquement au démarrage.

## Retour arrière vers Alpha.18 — procédure obligatoire

Avant toute opération, sauvegarder Home Assistant, l'application et
`/data`. Conserver séparément hors du Raspberry Pi une copie datée des
bases SQLite, des nouvelles observations et des options Supervisor.

**Alpha.18 ne peut pas lire les préférences B3 v3.** Avant le retour arrière,
arrêter l'application et archiver intégralement le fichier
`/data/surplus_pv_user_settings.json` au format v3. Lire son champ
`legacy` : s'il contient un objet JSON, restaurer exactement cet objet
sous le nom `surplus_pv_user_settings.json` en conservant les permissions ;
s'il vaut `null`, laisser ce nom de fichier absent. Conserver le fichier
v3 archivé pour une éventuelle reprise vers Alpha.19.

**Ne pas restaurer automatiquement `.pre-b3.backup` :** après plusieurs
migrations cette sauvegarde peut contenir des valeurs plus anciennes que
l'objet `legacy` du fichier v3. Conserver les deux pièces comme preuves.
Restaurer ensuite le logiciel Alpha.18 dans **la même application**,
sans désinstallation destructive, et contrôler le slug, les options, les
consignes et les historiques avant tout fonctionnement.
Une restauration Home Assistant peut écraser les données acquises depuis
la sauvegarde ; conserver et réconcilier ces données récentes.
La procédure a été testée avec le lecteur de préférences Alpha.18,
mais **pas encore sur une installation HAOS réelle**.

La mise à jour et les essais logiciels ne constituent pas une validation physique
des contacts, des températures, des courants ou de la chaîne thermique.
