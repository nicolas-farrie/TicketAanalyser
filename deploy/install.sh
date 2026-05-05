#!/bin/bash
# Installation de TicketUanalyser sur serveur Linux
# À lancer en root depuis le dossier du projet

set -e

INSTALL_DIR=/opt/ticketanalyser
SAMBA_DIR=/srv/samba/tickets
SERVICE_USER=ticketanalyser

echo "=== Installation TicketUanalyser ==="

# Créer l'utilisateur système dédié (sans login shell)
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "[OK] Utilisateur $SERVICE_USER créé"
fi

# Créer et peupler le répertoire d'installation
mkdir -p "$INSTALL_DIR"
cp -r ../*.py ../config.ini.example "$INSTALL_DIR/"
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    cp "$INSTALL_DIR/config.ini.example" "$INSTALL_DIR/config.ini"
    echo "[!] Pensez à renseigner $INSTALL_DIR/config.ini (host, user, password, dir_path)"
fi

# Créer le virtualenv et installer les dépendances
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet pdfplumber PyPDF2 pymysql streamlit

# Créer le dossier Samba de dépôt
mkdir -p "$SAMBA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$SAMBA_DIR"
chmod 775 "$SAMBA_DIR"

# Droits sur le répertoire d'installation
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Installer les unités systemd
cp ticketu-analyser.path   /etc/systemd/system/
cp ticketu-analyser.service /etc/systemd/system/
cp ticketu-dashboard.service /etc/systemd/system/

# Mettre à jour le chemin Samba dans les unités si différent de la valeur par défaut
if [ "$SAMBA_DIR" != "/srv/samba/tickets" ]; then
    sed -i "s|/srv/samba/tickets|$SAMBA_DIR|g" /etc/systemd/system/ticketu-analyser.path
fi

systemctl daemon-reload
systemctl enable --now ticketu-analyser.path
systemctl enable --now ticketu-dashboard.service

echo ""
echo "=== Installation terminée ==="
echo "  Dossier de dépôt  : $SAMBA_DIR"
echo "  Dashboard          : http://$(hostname -I | awk '{print $1}'):8501"
echo "  Logs analyseur     : journalctl -u ticketu-analyser.service -f"
echo "  Logs dashboard     : journalctl -u ticketu-dashboard.service -f"
echo ""
echo "  Vérifier la config : $INSTALL_DIR/config.ini"
