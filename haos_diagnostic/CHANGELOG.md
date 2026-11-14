# Historique des versions de l'application Surplus PV

## 1.0.0-alpha.13 — candidat

Courbe mesurée obligatoire pour AUTO réel, surveillance de la régulation et temporisations watchdog explicites. Aucun redémarrage automatique de commande.

## 1.0.0-alpha.12 — lecture thermique et jauge de consigne

- Lecture de la température, unité et date de rapport via le modèle HA vivant ; échec fermé si la réponse est indisponible ou invalide. Les droits et la sonde doivent être vérifiés sur l’installation.
- Jauge informative de la puissance demandée, avec identification simulation et banc ; état physique non vérifié.
- Conservation des limites thermiques, du heartbeat, des options et des données.

## 1.0.0-alpha.11 — correctif de surveillance thermique, 08.11.2026

- Une seule mesure horodatée validée par la boucle thermique est réutilisée par le calcul
  de confort et la régulation ; suppression du second GET pouvant diverger.
- Périmée (>60 s), future (<-2 s), indisponible ou hors plage : inhibition
  maintenue ; le heartbeat thermique garde son défaut mémorisé.
- Diagnostic visible sur Boiler : entité, état, âge du rapport HA, horodatage,
  résultat du service HA update_entity et raison de rejet.
- Correction de la limite d'âge affichée dans Boiler : celle de la sonde,
  et non celle du compteur réseau. Journal : température réelle au lieu de « demo 45 ».
- Aucune extension du délai, aucun GPIO ou DAC activé, aucun accès 1-Wire direct.
- Essais logiciel CI et HAOS/ARM64 requis ; absence de mesure HA lors d'une
  panne d'intégration 1-Wire reste une cause d'inhibition légitime.

## 1.0.0-alpha.10 — candidat de publication, 08.11.2026

- Surplus PV peut demander lui-même le rafraîchissement de l'entité
  `sensor.temperature_boiler` via l'action HA `homeassistant.update_entity`.
- Fonction facultative, désactivée par défaut ; interrogation bornée à
  15 secondes par défaut (10–120 s configurables), sans répétition en rafale.
- Le résultat de l'action ne remplace jamais la validation de la température :
  `last_reported`/`last_updated`, unité °C, âge maximal et limite thermique
  restent surveillés. Un défaut heartbeat antérieur reste mémorisé.
- Aucun accès direct nouveau au bus 1-Wire, aucun privilège supplémentaire,
  aucune commande de sortie déclenchée par le rafraîchissement.
- Conservation du slug, de la planification de confort historique, de SQLite,
  des modes MANUEL/AUTO et de l'inhibition matérielle.
- Tests logiciels et CI requis avant publication ; mise à jour sur Raspberry Pi
  et validation électrique indépendantes.

## 1.0.0-alpha.9 — candidat

- Heartbeat conditionné aux boucles thermiques et de garde DAC indépendantes
  de l'acquisition gPlugK ; une consigne AUTO expirée est inhibée.
- Fraîcheur de la température boiler séparée du compteur, 60 s par défaut.
- Sélecteur mobile SW-011 : interaction préservée pendant le polling.
- Simulations uniquement ; temporisation et protections matérielles non validées.

## 1.0.0-alpha.8 — candidat non publié

- Diagnostic de communication gPlugK séparé des erreurs thermiques et de régulation.
- DAC non initialisé indiqué comme inactif ; erreurs et refus explicités.
- Interface Boiler simplifiée pour le raccordement configuré `boiler`.

## 1.0.0-alpha.7 — candidat non publié

- Configuration technique distincte pour le watchdog ; commande Boiler accessible depuis l'accueil.
- Carte Boiler dans les entrées, avec distinction des demandes et de l'état physique inconnu.
- Sonde hors boiler retirée de l'interface ; historique conservé.
- MANUEL logiciel indépendant du watchdog ; la sélection matérielle reste à vérifier.

## 1.0.0-alpha.5 — candidat SW-006

- Retrait des pages Énergie et diagnostic ; acquisitions, prévisions et historiques conservés.
- Graphiques simplifiés, sélection préservée au rafraîchissement et séries denses prises en charge.
- Sonde 1-Wire hors boiler via entité HA : état, rapports horodatés et historique SQLite additif.
- AUTO demeure virtuel ; MANUEL réel exige une température d'eau valide, indépendante de la sonde à l'air.
- Slug, Ingress, réglages, historiques et arbitre de sortie conservés. Accès GPIO4/owserver à vérifier sur le Pi.

## 1.0.0-alpha.4 — 04.11.2026

- Pilote GP8600 I²C réel 16 bits 0–20 mA, adresse configurable et simulateur par défaut.
- Page Boiler : modes exclusifs AUTO / MANUEL / BANC DAC ; curseurs immédiats,
  garde thermique et retour AUTO à cible atteinte ; aucune reprise au redémarrage.
- Banc isolé : relevés multimètre persistants, écarts et calibration linéaire proposée,
  sans correction appliquée ni mesure physique inventée.
- AUTO conserve la régulation virtuelle, le confort et les projections 48 h.
- Même slug, réglages et historiques préservés ; seul accès matériel ajouté : /dev/i2c-1.
- Publication logicielle ; essais banc et réception réelle Pi/CELDUC non réalisés.
  Conserver dac_driver=simulated jusqu’à validation physique distincte.

## 1.0.0-alpha.3 — 31.10.2026

- Acquisition optionnelle Sofar via Home Assistant : puissance AC, production du jour et index total, avec contrôle des unités et de la fraîcheur.
- Courbes PV et bilans jour/mois/année dans la page Énergie existante ; couverture partielle et anomalies explicites.
- Autoconsommation et consommation maison calculées uniquement après confirmation de topologie et synchronisation des mesures.
- Historique PV SQLite additif, uniquement si configuré ; données antérieures et réglages conservés. Aucun passé inventé.
- Mise à jour en place depuis alpha.2 ; même slug, ARM64, Ingress et permissions. Sauvegarde préalable requise ; voir DOCS.md.
- Validation logicielle en simulation et construction ARM64 ; réception réelle sur Raspberry Pi non effectuée. Aucune commande de puissance.

## 1.0.0-alpha.2 — publiée le 29.10.2026

- Nouvelle page Énergie : puissance réseau signée, fraîcheur HA et historique jour/mois/année en Europe/Zurich.
- Bilans import/export estimés par intégration des puissances couvertes ; lacunes et couverture partielle explicites.
- Production PV réelle et autoconsommation indisponibles tant que Sofar et la topologie ne sont pas validés ; options PV facultatives préparées.
- Réutilisation de SQLite et de son archivage permanent, sans migration, purge ou réinitialisation des préférences.
- Planification de confort et prévisions 48 h conservées ; aucune commande physique.
- Mise à jour en place depuis 1.0.0-alpha.1 dans la même application et le même dépôt, après sauvegarde ; aucune réinstallation ni migration d'une ancienne application locale.
- Publication et essai sur Raspberry Pi soumis à autorisations distinctes. Voir DOCS.md du paquet pour la sauvegarde et le retour arrière.

## 1.0.0-alpha.1 — version initiale publiée le 28.10.2026

- Reprise fonctionnelle de `0.7.0-comfort-test1` : lecture gPlugK, confort automatique simulé, prévisions Forecast.Solar et PAC, graphique de trajectoire 48 h.
- Synchronisation des versions `config.yaml`, API et Dockerfile.
- Essais automatisés Python, JavaScript, navigateur et construction ARM64 sur la Pull Request SW-001.
- **Aucune commande de chauffe matérielle :** `physical_outputs=NOT_IMPLEMENTED`.
- La migration depuis la version locale doit préserver les réglages et SQLite. Le changement de canal d'installation n'est pas automatique : consulter `docs/20_LOGICIEL/MISES_A_JOUR_HOME_ASSISTANT_SW003.md` dans le dépôt source.

## 0.7.0-comfort-test1 — socle antérieur
- Planification de confort Europe/Zurich, échéance horaire, appoint réseau simulé seulement sur autorisation, repli prudent, trajectoire 48 h.
- Version historique conservée pour le retour arrière.
# 1.0.0-alpha.6 — candidat

- Heartbeat GPIO17 sélectionnable et surveillance des tâches, arrêt par défaut.
- Modes DAC AUTO/MANUEL réels sélectionnables, sans reprise de consigne.
- Conversion de puissance provisoire et inhibition matérielle non qualifiée.
