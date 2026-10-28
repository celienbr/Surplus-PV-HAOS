# Historique des versions de l'application Surplus PV

## 1.0.0-alpha.1 — candidate, non déployée

- Reprise fonctionnelle de `0.7.0-comfort-test1` : lecture gPlugK, confort automatique simulé, prévisions Forecast.Solar et PAC, graphique de trajectoire 48 h.
- Synchronisation des versions `config.yaml`, API et Dockerfile.
- Essais automatisés Python, JavaScript, navigateur et construction ARM64 sur la Pull Request SW-001.
- **Aucune commande de chauffe matérielle :** `physical_outputs=NOT_IMPLEMENTED`.
- La migration depuis la version locale doit préserver les réglages et SQLite. Le changement de canal d'installation n'est pas automatique : consulter `docs/20_LOGICIEL/MISES_A_JOUR_HOME_ASSISTANT_SW003.md` dans le dépôt source.

## 0.7.0-comfort-test1 — socle antérieur
- Planification de confort Europe/Zurich, échéance horaire, appoint réseau simulé seulement sur autorisation, repli prudent, trajectoire 48 h.
- Version historique conservée pour le retour arrière.
