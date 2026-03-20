# TODO — TicketUanalyser

## Fait (session 2026-03-20)

- [x] Correction des bugs hérités (import pylogo, parser_name, sauvegarder_ticket)
- [x] Activation parser_name dans Ticket + BD (traçabilité du format)
- [x] CLI argparse : --path/-p, --config/-c, --dry-run, --help
- [x] Migrations propres via INFORMATION_SCHEMA (plus d'exception silencieuse)
- [x] Dashboard Streamlit : KPIs, évolution mensuelle, top articles, rayons, qualité
- [x] Double support BD : MariaDB (interne) + SQLite (distribution locale)
- [x] Archivage PDF en blob + déduplication par SHA-256 (table fichiers_pdf)
- [x] Couche d'abstraction database.py (BaseDatabase, MariaDBDatabase, SQLiteDatabase)

---

## Court terme

- [ ] Mode service systemd + dossier partagé Samba (si déploiement serveur familial)
- [ ] `on_duplicate = skip|update` dans config.ini pour mode non-supervisé (sans input interactif)

## Moyen terme

- [ ] Nouveaux parsers enseignes
  - Hériter `BaseTicketParser`, implémenter 3 méthodes, ajouter dans `self.parsers`
  - Le plus gros du travail : analyser le format PDF de chaque enseigne
- [ ] Auth basique sur le dashboard Streamlit (si multi-utilisateurs)

## Distribution locale (Windows / Linux)

- [ ] SQLite déjà supporté via `config.ini` → `type = sqlite`
- [ ] Installeur = `pip install` + `.bat` (Windows) ou `.sh` (Linux) de lancement
- [ ] Tester l'install from scratch sur un poste vierge
