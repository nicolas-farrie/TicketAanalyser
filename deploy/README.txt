TicketUanalyser — Déploiement serveur
======================================

Prérequis
---------
- Linux (Debian/Ubuntu recommandé)
- Python 3.10+
- MariaDB installé et démarré
- Samba installé et configuré (voir ci-dessous)
- Accès root pour l'installation


Fichiers présents
-----------------
  install.sh                Script d'installation automatique (à lancer en root)
  ticketu-analyser.path     Unité systemd : surveille le dossier Samba
  ticketu-analyser.service  Unité systemd : lance l'analyse quand un PDF arrive
  ticketu-dashboard.service Unité systemd : dashboard Streamlit (port 8501)


Installation rapide
-------------------
1. Copier le projet sur le serveur :
     scp -r /chemin/local/TicketUanalyser user@serveur:/tmp/

2. Sur le serveur, lancer l'installeur :
     cd /tmp/TicketUanalyser/deploy
     sudo bash install.sh

3. Editer la configuration :
     sudo nano /opt/ticketanalyser/config.ini

   Renseigner :
     [database]
     host     = localhost        (ou IP du serveur MariaDB)
     user     = ticketu
     password = MOT_DE_PASSE
     database = tickets_u

     [ticketsdir]
     dir_path     = /srv/samba/tickets
     on_duplicate = skip

4. Redémarrer le service après avoir modifié la config :
     sudo systemctl restart ticketu-analyser.path
     sudo systemctl restart ticketu-dashboard.service


Préparer MariaDB
----------------
  sudo mariadb
  > CREATE DATABASE tickets_u CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  > CREATE USER 'ticketu'@'localhost' IDENTIFIED BY 'MOT_DE_PASSE';
  > GRANT ALL PRIVILEGES ON tickets_u.* TO 'ticketu'@'localhost';
  > FLUSH PRIVILEGES;

  La première exécution du script crée automatiquement les tables.


Configurer Samba (partage réseau)
----------------------------------
Ajouter dans /etc/samba/smb.conf :

  [tickets]
  path = /srv/samba/tickets
  browseable = yes
  read only = no
  guest ok = no
  valid users = @sambashare

Créer un utilisateur Samba pour chaque membre de la famille :
  sudo smbpasswd -a prenom

Redémarrer Samba :
  sudo systemctl restart smbd


Vérifications
-------------
  # État des services
  sudo systemctl status ticketu-analyser.path
  sudo systemctl status ticketu-dashboard.service

  # Logs en temps réel
  sudo journalctl -u ticketu-analyser.service -f
  sudo journalctl -u ticketu-dashboard.service -f

  # Tester manuellement (dépose un PDF et vérifie)
  sudo systemctl start ticketu-analyser.service


Accès au dashboard
------------------
Ouvrir dans un navigateur sur le réseau local :
  http://IP_DU_SERVEUR:8501

Pour trouver l'IP du serveur :
  hostname -I


Comportement on_duplicate
--------------------------
  ask    — demande interactivement à chaque doublon (mode local uniquement)
  skip   — ignore les doublons silencieusement (recommandé en mode service)
  update — écrase les doublons existants en base


Mise à jour du logiciel
------------------------
  sudo systemctl stop ticketu-analyser.path ticketu-dashboard.service
  sudo cp /nouveau/TicketUanalyser/*.py /opt/ticketanalyser/
  sudo chown ticketanalyser:ticketanalyser /opt/ticketanalyser/*.py
  sudo systemctl start ticketu-analyser.path ticketu-dashboard.service
