# Surplus PV — Home Assistant OS 1.0.0-alpha.22

Surplus PV utilise les mesures Home Assistant, les prévisions et le planificateur
confort. Le GP8600 peut commander le chauffe-eau après configuration.
La puissance affichée est une demande, non une mesure physique.

## Mise à jour depuis alpha.21

1. Dans Paramètres → Système → Sauvegardes, sauvegarder l'application et ses
   données ; conserver une copie de la sauvegarde hors du Raspberry Pi.
2. Dans Paramètres → Applications → Boutique, rechercher les mises à jour,
   ouvrir l'application Surplus PV existante et choisir Mettre à jour. Ne pas
   désinstaller l'application : son slug reste `surplus_pv_diagnostic`.
3. Vérifier la version 1.0.0-alpha.22, les réglages de confort et les historiques.
   Les bases SQLite et les préférences restent dans /data.

Le tableau de bord indique six diagnostics indépendants. Une sonde manquante
peut produire une estimation de confort basée sur 35 °C : ce n'est jamais une
mesure et elle n'autorise pas la chauffe. Si le calcul AUTO échoue, un repli à
zéro ne garantit pas le confort. Un défaut DAC en AUTO bloque le heartbeat et
requiert une reprise explicite. Un accusé I²C ne mesure pas le courant réel.

Dans Pilotage, la consigne AUTO utilisateur est bornée par le plafond
`boiler_max_temp_c` des options Supervisor. Le choix Prévision réutilise le
calcul Forecast.Solar ; si les données manquent ou sont périmées, le repli
indiqué à l'écran s'applique. La prévision ne garantit pas le surplus. Les
préférences de confort dans l'interface sont verrouillées si
`use_addon_settings` est activé ; les options Supervisor font alors autorité.

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
ordre de chauffe n'est repris au démarrage. Le dernier mode explicitement choisi
est mémorisé : sans historique valide, ARRÊT est retenu ; AUTO reprend après les
contrôles de sécurité et une mesure réseau nouvelle ; MANUEL conserve ses réglages
mais attend une nouvelle commande explicite. Un défaut DAC mémorisé exige une
récupération explicite. Une température supérieure à 83 °C est invalide.

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
quand un fichier existait. Sans plafond configuré, la commande réelle reste
inhibée. Les réglages MANUEL mémorisés ne sont pas réappliqués au démarrage.

## Retour arrière

Pour revenir d'Alpha.22 à Alpha.21, arrêter l'application et sauvegarder tout
`/data`, y compris SQLite et les fichiers WAL/SHM, dans une copie hors du Pi.
Alpha.21 ne comprend pas les champs `requested_mode` et `dac_fault_latched` de
`b3` : conserver une copie intacte du fichier v3 récent, puis, sur une copie
destinée à Alpha.21, retirer uniquement ces deux champs sans modifier `legacy`
ni les autres préférences. Vérifier le JSON et ses droits avant restauration.
Alpha.21 demande AUTO à chaque démarrage : avant de la démarrer, faire désactiver
`heartbeat_autostart` dans Supervisor et assurer une mise en sécurité indépendante
de l'installation par une personne habilitée. Vérifier options, historique et état
des sorties ; une restauration HA peut remplacer les données récentes, à préserver
et réconcilier séparément. Ne pas désinstaller l'application.

Pour un retour supplémentaire vers Alpha.18, arrêter l'application et sauvegarder intégralement
ses données, dont SQLite, options et `/data/surplus_pv_user_settings.json` v3.
Conserver le fichier v3 dans une archive distincte. Alpha.18 ne lit pas v3.

Lire le champ `legacy` du fichier v3 conservé. S'il contient un objet, restaurer
cet objet JSON complet sous le nom `/data/surplus_pv_user_settings.json`, avec
les droits du fichier d'origine, puis vérifier sa lecture par Alpha.18. Si
`legacy` vaut null, ne pas créer de fichier de préférences sous ce nom.
Ne pas restaurer automatiquement `.pre-b3.backup` : cette sauvegarde peut
précéder une réinstallation et contenir des valeurs plus anciennes que `legacy`.
La conserver et la comparer au besoin, sans écraser les copies archivées.

Revenir à Alpha.18 avec la procédure de restauration de la même application,
sans désinstaller ni supprimer `/data`. Une restauration HA peut remplacer les
données acquises depuis la sauvegarde : préserver séparément ces données et
les réconcilier. Vérifier le slug, les options, l'historique et les réglages.
La conservation du fichier v3 ne rend pas Alpha.18 compatible avec son contenu ;
les champs B3 restent archivés pour une reprise ou conversion contrôlée.

La mise à jour et les essais logiciels ne constituent pas une validation physique
des contacts, des températures, des courants ou de la chaîne thermique.
