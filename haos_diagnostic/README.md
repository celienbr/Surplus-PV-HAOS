# Surplus PV — Home Assistant OS 1.0.0-alpha.31

Surplus PV utilise les mesures Home Assistant, les prévisions et le planificateur
confort. Le GP8600 peut commander le chauffe-eau après configuration.
La puissance affichée est une demande, non une mesure physique.

## Mise à jour depuis Alpha.30

1. Dans Paramètres → Système → Sauvegardes, sauvegarder l'application et ses
   données ; conserver une copie de la sauvegarde hors du Raspberry Pi.
2. Dans Paramètres → Applications → Boutique, rechercher les mises à jour,
   ouvrir l'application Surplus PV existante et choisir Mettre à jour. Ne pas
   désinstaller l'application : son slug reste `surplus_pv_diagnostic`.
3. Vérifier la version 1.0.0-alpha.31, les réglages de confort et les historiques.
   Les bases SQLite et les préférences restent dans /data.

Au démarrage, AUTO / Prévision est sélectionné et la chauffe reprend après
validation des mesures et sécurités. Un ARRÊT volontaire reste mémorisé.
Avec une provenance des préférences ambiguë, la demande reste à zéro.
L’appoint réseau dépend du réglage grid_backup_enabled. Après le dernier départ
prudent, un cycle autorisé monte progressivement vers la puissance maximale,
puis s’arrête au minimum de confort ou à la fin de la période de grâce.

L’appoint réseau autorisé et dû peut fonctionner sans gPlugK. Le défaut du
compteur reste indiqué. Les limites et la rampe restent actives.

Le tableau de bord indique six diagnostics indépendants. Une estimation de
confort à 35 °C reste indicative et ne peut autoriser la chauffe. Un défaut DAC
dans un mode physique suspend la chauffe et inhibe le heartbeat. Le journal
conserve les transitions de défaut et de récupération.

Le service réessaie la liaison à 0 mA. Trois initialisations acquittées et
espacées de cinq secondes sont requises. Après un échec, le délai augmente
jusqu’à 60 secondes. Une fois le défaut effacé, la reprise vérifie de nouveau
le watchdog, la sonde du boiler et les limites thermiques. AUTO utilise les mesures gPlugK pour le surplus ; l’appoint confort dû et autorisé reste indépendant du compteur. La courbe puissance/courant validée et la rampe restent requises. Pendant la même session, MANUEL peut reprendre la demande explicitement
réglée pour la sélection en cours, sous les mêmes gardes. Une nouvelle sélection
MANUEL repart à 0 %, en conservant les préférences. À la consigne, la demande
MANUEL s’arrête et AUTO / Prévision reprend après de nouvelles vérifications. ARRÊT annule la reprise et
la vérification du DAC peut continuer à zéro.

Un ACK I²C confirme un échange de données. L’identité du DAC, le courant
analogique et la puissance ne sont pas mesurés. Une absence de communication
est détectable au cours d’un échange. Un DAC au repos peut afficher A002 OK
avec la mention « liaison non vérifiée en continu » dans DIAGNOSTIC.

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
Si cette option est activée, le heartbeat peut aussi démarrer en ARRÊT,
avec une demande de courant nulle. La récupération exige des tâches critiques
à jour et une impulsion logicielle complète de GPIO17. Un relevé électrique
séparé est nécessaire pour connaître le signal effectivement reçu par
l’Omron et l’état des relais. Sans configuration valide, la demande de chauffe
reste mémorisée et suspendue. Une température supérieure à 83 °C est invalide.

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
inhibée. Le format v3 et la base SQLite sont conservés.

## Retour arrière vers Alpha.30

Demander ARRÊT, arrêter l'application, sauvegarder intégralement /data et conserver les acquisitions récentes. Restaurer le code Alpha.30 de la même application sans désinstallation ; ne pas écraser SQLite ni les préférences récentes. Vérifier ARRÊT et les réglages avant reprise.

## Retour arrière vers Alpha.28

Arrêter l’application et réaliser une sauvegarde complète vérifiée. Démarrer Alpha.28 en ARRÊT avec les préférences B3 et les bases actives dans /data. Ne pas désinstaller l’application ni restaurer d’anciennes bases sur les acquisitions récentes.

## Retour arrière vers Alpha.27

Demander ARRÊT, sauvegarder l'application et tout /data hors du Pi, y compris
SQLite, WAL/SHM et préférences. Conserver les données récentes séparément.
Restaurer la sauvegarde Alpha.27 de la même application sans la désinstaller,
puis vérifier les réglages et réconcilier les données récentes. La restauration
réelle sur le Pi n'a pas été exécutée par cette validation.

## Retour arrière vers Alpha.26

Demander ARRÊT, arrêter l’application et sauvegarder hors du Pi les données
actuelles, dont SQLite, WAL/SHM et préférences. Conserver séparément les
données acquises sous Alpha.27. Restaurer le code Alpha.26 de la même
application sans désinstallation, vérifier ARRÊT et réconcilier les données
récentes avant toute restauration. Le retour arrière réel reste à valider.

Pour revenir d’Alpha.26 à Alpha.25, demander ARRÊT et vérifier qu’il est
enregistré. Arrêter l’application. Sauvegarder l’application et tout
`/data`, dont les préférences, SQLite et ses fichiers WAL/SHM, avec une
copie hors du Pi. Conserver les données récentes séparément avant de
restaurer la version Alpha.25 de la même application sans la désinstaller.
Alpha.25 ignore le marqueur `.stop` utilisé lors d’un échec d’écriture.
Vérifier que le fichier v3 destiné à Alpha.25 indique `requested_mode:
ARRET` avant son redémarrage. Vérifier ensuite les options et l’historique
et réconcilier les données plus récentes. Le retour arrière réel reste à tester.

Pour revenir d'Alpha.25 à Alpha.24, arrêter l’application et conserver une
sauvegarde actuelle de /data, SQLite avec WAL/SHM, préférences et historique,
ainsi qu’une copie hors Pi. Restaurer la sauvegarde Alpha.24 de la même
application sans désinstallation. Une restauration peut remplacer les données
récentes : les conserver et les réconcilier séparément. Vérifier les paramètres
et historiques avant reprise ; retour arrière réel non testé.

Pour revenir d'Alpha.24 à Alpha.23, arrêter l'application, sauvegarder `/data`,
SQLite avec WAL/SHM et les réglages hors Pi, et préserver les données récentes.
Restaurer la même application sans désinstallation ; une sauvegarde restaurée
peut écraser les données plus récentes, à réconcilier séparément. Vérifier les
options et l'historique avant reprise. Ce retour n'a pas été validé sur Pi.

Pour revenir d'Alpha.23 à Alpha.22, arrêter l'application et sauvegarder `/data`
et toutes les données récentes, y compris SQLite et ses fichiers WAL/SHM, avec
une copie hors Pi. Restaurer la version Alpha.22 de la même application sans
désinstallation. Les préférences conservent le même format ; une restauration HA
peut remplacer les données récentes, à préserver et réconcilier séparément.
Vérifier les réglages et l'historique avant la remise en route. Ce retour arrière
n'a pas été exécuté sur une installation réelle.

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

Les protections indépendantes TH1/STB1, le watchdog Omron et le secours S2
gardent leur rôle matériel. Le propriétaire a déclaré le 21 novembre 2026 que le boiler et son circuit sont raccordés, que la sonde fonctionne, que la chauffe a été vérifiée et que ses validations matérielles sont terminées sans réserve restante selon sa déclaration. Les anciennes réserves B01–B10 restent traçables ; leur rapprochement documentaire avec les pièces disponibles reste à effectuer. Les essais logiciels ne mesurent ni les contacts, ni le courant analogique, ni la chaîne thermique.

La barre de confort estime la commande depuis minuit en Europe/Zurich. Le cumul est indisponible en cas de lacune ou de redémarrage ; l'origine PV/réseau n'est pas établie. Ce n'est pas une mesure physique.
