# Surplus PV — Home Assistant OS 1.0.0-alpha.15

Surplus PV utilise les mesures Home Assistant, les prévisions et le planificateur
confort 48 h. Le GP8600 peut commander le chauffe-eau après configuration.
La puissance affichée est une demande, non une mesure physique.

## Mise à jour depuis alpha.14

1. Dans Paramètres → Système → Sauvegardes, sauvegarder l'application et ses
   données ; conserver une copie de la sauvegarde hors du Raspberry Pi.
2. Dans Paramètres → Applications → Boutique, rechercher les mises à jour,
   ouvrir l'application Surplus PV existante et choisir Mettre à jour. Ne pas
   désinstaller l'application : son slug reste `surplus_pv_diagnostic`.
3. Vérifier la version 1.0.0-alpha.15, les réglages de confort et les historiques.
   Les bases SQLite et les préférences restent dans /data.

## Configuration réelle

Renseigner `dac_driver: gp8600`, `dac_connection: boiler`, l'adresse I²C et
`boiler_temperature_entity` de la sonde d'eau. Pour AUTO réel, saisir les couples
mesurés `[W, mA]` strictement croissants dans `power_curve_points`, couvrant
la plage de puissance configurée, puis confirmer par `power_curve_confirmed`.
Sans courbe validée, AUTO réel reste indisponible. Un accusé I²C ne mesure ni le
courant ni la puissance, et zéro demandé ne prouve pas une coupure physique.

Pour démarrer le watchdog avec le service, définir `heartbeat_autostart: true`,
`heartbeat_period_s`, `heartbeat_width_s`, `heartbeat_task_timeout_s` et
`heartbeat_omron_setting_s` selon le matériel. La période et la durée d'impulsion
sont nulles par défaut : les renseigner avec les valeurs établies sur l'installation.
Le réglage Omron doit dépasser la somme du timeout et de la période ; le timeout
est au moins deux fois l'intervalle d'échantillonnage. L'impulsion doit durer au
moins 50 ms et le repos au moins 100 ms. Une configuration incomplète empêche
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

## Retour arrière

Sauvegarder d'abord les données et journaux récents. Dans Paramètres → Système →
Sauvegardes, restaurer la sauvegarde alpha.14 de la même application. Cette
restauration remplace les données par leur état sauvegardé : préserver séparément
les acquisitions postérieures. Ne pas supprimer /data ni désinstaller l'add-on.

La mise à jour et les essais logiciels ne constituent pas une validation physique
des contacts, des températures, des courants ou de la chaîne thermique.
