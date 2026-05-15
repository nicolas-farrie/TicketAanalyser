#!/bin/bash
# Installation de TicketUanalyser sur serveur Linux
# Peut être lancé depuis n'importe où : sudo bash deploy/install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR=/opt/ticketanalyser
SAMBA_DIR=/srv/samba/tickets
SERVICE_USER=ticketanalyser
SAMBA_SHARE_NAME=tickets

echo "=== Installation TicketUanalyser ==="

# Installer les dépendances système
apt-get install -y python3 python3-venv samba docker-compose-plugin

# Créer l'utilisateur système dédié (sans login shell)
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "[OK] Utilisateur $SERVICE_USER créé"
fi

# Créer et peupler le répertoire d'installation
mkdir -p "$INSTALL_DIR"
if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
    cp -r "$REPO_DIR"/*.py "$REPO_DIR"/config.ini.example "$INSTALL_DIR/"
fi
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    cp "$INSTALL_DIR/config.ini.example" "$INSTALL_DIR/config.ini"
    echo "[!] Pensez à renseigner $INSTALL_DIR/config.ini (host, user, password, dir_path)"
fi

# Créer le virtualenv et installer les dépendances Python
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install pdfplumber PyPDF2 pymysql streamlit plotly

# Créer le dossier de dépôt des tickets (local sur ce serveur)
mkdir -p "$SAMBA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$SAMBA_DIR"
chmod 2775 "$SAMBA_DIR"  # setgid : les nouveaux fichiers héritent du groupe

# Créer le groupe sambashare si absent et y ajouter le service user
getent group sambashare &>/dev/null || groupadd sambashare
usermod -aG sambashare "$SERVICE_USER"

# Droits sur le répertoire d'installation
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Déployer MariaDB via Docker (docker-compose.yml dans INSTALL_DIR)
cp "$SCRIPT_DIR/docker-compose.yml" "$INSTALL_DIR/"
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "[!] Renseigner les mots de passe dans $INSTALL_DIR/.env avant de continuer"
    echo "    Puis relancer : docker compose -f $INSTALL_DIR/docker-compose.yml up -d"
else
    docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d
    echo "[OK] Conteneur MariaDB démarré"
fi

# Configurer le partage Samba (ajout idempotent)
SMB_CONF=/etc/samba/smb.conf
if ! grep -q "\[$SAMBA_SHARE_NAME\]" "$SMB_CONF"; then
    cat >> "$SMB_CONF" <<EOF

[$SAMBA_SHARE_NAME]
   path = $SAMBA_DIR
   browseable = yes
   read only = no
   valid users = @sambashare
   force group = sambashare
   force user = $SERVICE_USER
   create mask = 0664
   directory mask = 2775
EOF
    echo "[OK] Partage Samba [$SAMBA_SHARE_NAME] ajouté"
fi
systemctl enable --now smbd nmbd
systemctl restart smbd

# Installer les unités systemd
cp "$SCRIPT_DIR/ticketu-analyser.service" /etc/systemd/system/
cp "$SCRIPT_DIR/ticketu-dashboard.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now ticketu-dashboard.service

echo ""
echo "=== Installation terminée ==="
echo "  Partage Samba      : \\\\$(hostname -s)\\${SAMBA_SHARE_NAME}"
echo "  Dossier local      : $SAMBA_DIR"
echo "  Dashboard          : http://$(hostname -I | awk '{print $1}'):8501"
echo "  Logs analyseur     : journalctl -u ticketu-analyser.service -f"
echo "  Logs dashboard     : journalctl -u ticketu-dashboard.service -f"
echo ""
echo "  Vérifier la config : $INSTALL_DIR/config.ini"
echo "  Ajouter un user    : smbpasswd -a <prenom>"
