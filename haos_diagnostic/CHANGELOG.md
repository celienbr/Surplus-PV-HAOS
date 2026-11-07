# Historique des versions de l'application Surplus PV

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
