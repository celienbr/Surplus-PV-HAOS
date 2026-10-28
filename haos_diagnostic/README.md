# Surplus PV — Application Home Assistant OS (alpha)

Version 1.0.0-alpha.1 : lecture des mesures gPlugK, simulation du chauffe-eau,
prévisions solaires/PAC et planification de confort sur 48 heures.

AUCUNE COMMANDE PHYSIQUE : pas de GPIO, I2C, DAC, CELDUC, watchdog ni
commande Home Assistant d'écriture. Ne convient pas au raccordement 400 V.

Installation : Home Assistant > Paramètres > Applications > Dépôts,
ajouter https://github.com/celienbr/Surplus-PV-HAOS, puis installer
Surplus PV - Diagnostic (sans puissance) sur Home Assistant OS aarch64.

ATTENTION MIGRATION : une application installée depuis ce dépôt n'utilise
pas le même identifiant que l'ancienne application locale. Ne pas
désinstaller l'ancienne application ; sauvegarder d'abord ses paramètres
et données persistantes (SQLite), puis vérifier une migration contrôlée.

Les prochaines mises à jour diffusées dans ce même dépôt pourront être
proposées directement par Home Assistant.
