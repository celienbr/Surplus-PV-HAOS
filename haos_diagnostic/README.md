# Surplus PV — Home Assistant OS 1.0.0-alpha.8

Version 1.0.0-alpha.8 : GP8600, BANC DAC et MANUEL Boiler, simulation par défaut.
Application ARM64 (aarch64), simulation par défaut, GP8600 explicitement sélectionnable.
Tableau de bord : réseau gPlugK et prévisions. L'acquisition AC Sofar optionnelle
fournit les index de production et l'autoconsommation conditionnelle. Sans configuration,
PV réelle et autoconsommation restent indisponibles. Prévisions Forecast.Solar séparées des mesures.
Planification de confort et trajectoire 48 h conservées.

## Sonde du boiler

Configurer dans Home Assistant l'entité `boiler_temperature_entity` correspondant
à la sonde située dans l'eau. Une température absente ou périmée est inconnue ;
une valeur de démonstration n'autorise pas la commande réelle. Les données et
options historiques de sonde hors boiler sont conservées mais ne sont plus
affichées dans l'interface active.

GP8600 réel : dac_driver=gp8600, dac_i2c_address=88..95, bus /dev/i2c-1.
DAC 16 bits, 0–20 mA. Le heartbeat utilise GPIO17 ; aucun service HA ne commande K2.
dac_connection=bench : uniquement banc isolé sur résistance, jamais CELDUC.
dac_connection=boiler : AUTO et MANUEL réels exigent sélection explicite et
sonde réelle valide. AUTO exige un heartbeat GPIO sain ; MANUEL ne dépend pas
de son état logiciel. La sélection physique de la source dépend du câblage
et de l’Omron : une consigne MANUELLE ne prouve pas que le DAC pilote le CELDUC. Le courant 4–20 mA n'est pas une
mesure de puissance ; la caractéristique automatique est provisoire. À l'arrêt
ou à 0 % réel le logiciel demande zéro, mais une isolation matérielle de chauffe
indépendante reste à confirmer. La commutation Omron mode E n'est pas instantanée.
Au redémarrage : aucune consigne reprise ; la sortie physique reste inconnue
tant que les circuits et leur état ne sont pas vérifiés.
Erreur I²C : sortie inconnue, reprise explicite à zéro, aucune ancienne consigne
rejouée. Une coupure logicielle n'est pas une coupure électrique indépendante.
BANC DAC : curseur/saisie immédiats 0–20 mA, sortie activable ; aucune régulation,
prévision, température Boiler ou planification ne modifie ce courant. Relevés
multimètre stockés séparément dans /data/surplus_pv_dac.sqlite3 ; correction
linéaire proposée mais non appliquée sans revue des mesures et des extrémités.
Résistance nominale 250 Ω ±1 %, ≥0,5 W : 20 mA donne 5 V et 0,1 W.
La conformité de charge/alimentation et le montage isolé sont à vérifier en VAL.
La communication I²C ne prouve aucune mesure physique. Simulation identifiée.
Seuls /dev/i2c-1 et /dev/gpiochip0 sont exposés ; aucun privilège général ajouté. Le bus doit être
activé dans HAOS par le propriétaire selon la documentation officielle avant
un essai autorisé. Dockerfile inchangé : os/fcntl font partie de Python Linux.
Tests GitHub Actions sans matériel ; Pi, mesures et intégration non validés.

## Mise à jour depuis 1.0.0-alpha.7

1. Dans Home Assistant, noter la version, le dépôt et l'identifiant exact de
   l'application installée. Conserver le dépôt et la même application.
2. Toujours sauvegarder les données avant la mise à jour. Créer une sauvegarde Home Assistant incluant cette application et ses
   données, puis la télécharger hors du Pi. Conserver la clé du kit d'urgence
   si la sauvegarde est chiffrée. Vérifier que la sauvegarde est terminée.
   En cas de doute sur son contenu ou sa restauration, ne pas mettre à jour.
3. Relever les réglages, notamment confort, Forecast.Solar, PAC ; exporter
   l'historique utile. Le CSV seul ne remplace pas la sauvegarde SQLite.
   Si une copie manuelle de /data est réalisée par une méthode autorisée,
   arrêter l'application avant de copier la base et ses fichiers WAL/SHM ;
   ne jamais copier uniquement une base SQLite active.
4. Paramètres → Applications (ou Modules complémentaires) → Boutique →
   Rechercher les mises à jour, puis ouvrir l'application Surplus PV existante.
   Vérifier que la version proposée est 1.0.0-alpha.8, puis choisir Mettre à jour.
   Ne pas installer une deuxième application, changer d'URL de dépôt, ni
   désinstaller l'application en service. Si la version n'est pas proposée,
   vérifier la publication et actualiser la boutique ; ne pas réinstaller.
5. Après redémarrage, vérifier la version 1.0.0-alpha.8, les réglages conservés,
   les anciennes archives, le graphique 48 h et le planificateur de confort.
   Vérifier le signe import/export et la disponibilité des mesures. Le pilote doit rester simulé tant que la validation physique n’est pas autorisée.

Les tables SQLite antérieures sont conservées. Une table PV additive est créée
uniquement si une source PV est configurée. Données persistantes :
/data/surplus_pv_history.sqlite3 et /data/surplus_pv_user_settings.json.
Les options PV sont facultatives ; les trois entités sont vides par défaut.
Pour une installation Sofar déjà vérifiée dans Home Assistant, configurer sans
écraser les autres réglages :

    pv_actual_power_entity: sensor.inverter_power
    pv_actual_energy_today_entity: sensor.inverter_today_production
    pv_actual_energy_total_entity: sensor.inverter_total_production

Choisir la puissance AC (Power du profil sofar_g3, pas PV Power DC).
Confirmer pv_self_consumption_topology_verified uniquement si toute la production
AC est couverte, sans stockage ni autre flux non comptabilisé. La valeur par défaut
reste false ; une confirmation sur une installation ne vaut pas pour les autres.
Acquisition facultative toutes les 60 s, fraîcheur HA 120 s, réglables dans
pv_actual_sample_seconds et pv_actual_max_age_seconds. Les mesures réseau et
le confort n'attendent pas ces lectures. Le calcul nécessite deux canaux réseau
récents et un décalage PV/réseau de 15 s maximum. Les anomalies ne sont pas bornées
silencieusement à zéro. Les nouvelles observations sont conservées dans une table
SQLite additive ; désactiver les entités arrête la collecte sans effacer l'historique.
Publication logicielle 1.0.0-alpha.8 demandée par le propriétaire ; sauvegarde et mise
à jour Home Assistant effectuées par lui. Cette publication ne vaut pas validation physique. Les lectures et performances
sur Raspberry Pi restent à réceptionner après la mise à jour.
La fraîcheur se rapporte aux rapports HA, pas à l'horodatage physique du compteur.

## Retour arrière

En cas d'anomalie, arrêter l'application et conserver les journaux et les
nouvelles données. Home Assistant ne propose pas toujours un bouton de
rétrogradation : restaurer la sauvegarde préalable de la même application
avec sa version, sa configuration et ses données, via Paramètres → Système →
Sauvegardes, selon la version HA. Une restauration remplace les données par
leur état sauvegardé : préserver séparément les acquisitions postérieures
avant de restaurer. Ne pas écraser la sauvegarde d'origine, supprimer /data
ou désinstaller pour tenter un retour arrière. Si la restauration exige une
intervention supplémentaire, demander une procédure adaptée avant de continuer.

Le paquet ne contient ni réglage privé ni données réelles. L'absence de données
anciennes n'est jamais comblée artificiellement. Aucun essai sur Pi ni validation de mesures réelles n’est déduit des tests logiciels.
