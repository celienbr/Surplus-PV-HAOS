# Historique des versions de l'application Surplus PV

## 1.0.0-alpha.2 — 29.10.2026, candidate non publiée

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
