# Surplus PV — Home Assistant OS 1.0.0-alpha.3

Application ARM64 (aarch64), diagnostic et simulation uniquement. Tableau de
bord Énergie : réseau gPlugK, vues jour/mois/année et bilans estimés, couverture
partielle explicite. SW-004.2 ajoute l'acquisition AC Sofar optionnelle,
les index de production et l'autoconsommation conditionnelle. Sans configuration,
PV réelle et autoconsommation restent indisponibles. Prévisions Forecast.Solar séparées des mesures.
Planification de confort et trajectoire 48 h conservées.
Aucune commande physique : GPIO, I²C, DAC, CELDUC et relais non implémentés.

## Mise à jour depuis 1.0.0-alpha.2 (ou alpha.1)

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
   Vérifier que la version proposée est 1.0.0-alpha.3, puis choisir Mettre à jour.
   Ne pas installer une deuxième application, changer d'URL de dépôt, ni
   désinstaller l'application en service. Si la version n'est pas proposée,
   vérifier la publication et actualiser la boutique ; ne pas réinstaller.
5. Après redémarrage, vérifier la version 1.0.0-alpha.3, les réglages conservés,
   les anciennes archives, le graphique 48 h et le planificateur de confort.
   Ouvrir Énergie : signe import/export, fraîcheur et couverture de l'historique,
   navigation jour/mois/année. PV réelle/autoconsommation doivent rester
   indisponibles sans Sofar. Aucun pilotage physique ne doit apparaître.

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
La publication alpha.3 a été autorisée ; la mise à jour et sa sauvegarde sont
effectuées par le propriétaire depuis Home Assistant. Les lectures et performances
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
