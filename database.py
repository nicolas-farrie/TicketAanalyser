#!/usr/bin/env python3
"""
Couche d'abstraction base de données
Supporte MariaDB (client/serveur) et SQLite (installation locale)

config.ini :
    [database]
    type = mariadb   # ou sqlite
    # MariaDB
    host = localhost
    port = 3306
    database = tickets_u
    user = root
    password = secret
    # SQLite
    path = tickets.db
"""

import configparser
import os
import re
import sqlite3
from abc import ABC, abstractmethod

import pymysql
import pymysql.cursors


# ─── Configuration ────────────────────────────────────────────────────────────

def load_config(config_file: str = "config.ini") -> dict:
    """Charge la configuration depuis un fichier INI"""
    if not os.path.exists(config_file):
        _create_default_config(config_file)
    config = configparser.ConfigParser()
    config.read(config_file)
    return {
        'type':         config.get('database', 'type',         fallback='mariadb'),
        'host':         config.get('database', 'host',         fallback='localhost'),
        'port':         config.getint('database', 'port',      fallback=3306),
        'database':     config.get('database', 'database',     fallback='tickets_u'),
        'user':         config.get('database', 'user',         fallback='root'),
        'password':     config.get('database', 'password',     fallback=''),
        'path':         config.get('database', 'path',         fallback='tickets.db'),
        'dir_path':     config.get('ticketsdir', 'dir_path',   fallback=''),
        'on_duplicate': config.get('ticketsdir', 'on_duplicate', fallback='ask'),
    }


def _create_default_config(config_file: str):
    config = configparser.ConfigParser()
    config['database'] = {
        'type':     'mariadb',
        'host':     'localhost',
        'port':     '3306',
        'database': 'tickets_u',
        'user':     'root',
        'password': '',
        'path':     'tickets.db',
    }
    with open(config_file, 'w') as f:
        config.write(f)
    print(f"Fichier de configuration créé : {config_file}")
    print("Modifiez les paramètres selon votre configuration.")


# ─── Factory ──────────────────────────────────────────────────────────────────

def create_database(config: dict) -> 'BaseDatabase':
    """Retourne l'instance de BD selon config['type']"""
    db_type = config.get('type', 'mariadb').lower()
    db = SQLiteDatabase(config) if db_type == 'sqlite' else MariaDBDatabase(config)
    db.connect()
    db.init_schema()
    return db


# ─── Interface commune ────────────────────────────────────────────────────────

class BaseDatabase(ABC):

    def __init__(self, config: dict):
        self.config = config
        self._connection = None

    @abstractmethod
    def connect(self): pass

    @abstractmethod
    def close(self): pass

    @abstractmethod
    def execute(self, sql: str, params=None) -> int:
        """Exécute une requête — retourne lastrowid (utile pour INSERT)"""
        pass

    @abstractmethod
    def query(self, sql: str, params=None) -> list:
        """Exécute un SELECT — retourne une liste de dicts"""
        pass

    @abstractmethod
    def init_schema(self): pass

    @abstractmethod
    def column_exists(self, table: str, column: str) -> bool: pass

    # Fragments SQL dépendants du dialecte
    @abstractmethod
    def fmt_month(self, col: str) -> str:
        """Fragment SQL renvoyant 'YYYY-MM' depuis une colonne date"""
        pass

    @abstractmethod
    def fmt_week(self, col: str) -> str:
        """Fragment SQL renvoyant 'YYYY_WW' depuis une colonne date"""
        pass


# ─── MariaDB ──────────────────────────────────────────────────────────────────

class MariaDBDatabase(BaseDatabase):

    def connect(self):
        try:
            # Créer la base si elle n'existe pas (connexion sans database= pour éviter l'erreur)
            bootstrap = pymysql.connect(
                host=self.config['host'],
                port=self.config['port'],
                user=self.config['user'],
                password=self.config['password'],
                charset='utf8mb4',
                autocommit=True,
            )
            with bootstrap.cursor() as c:
                c.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config['database']}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            bootstrap.close()
            # Connexion principale avec database= : ping(reconnect=True) préserve la sélection
            self._connection = pymysql.connect(
                host=self.config['host'],
                port=self.config['port'],
                user=self.config['user'],
                password=self.config['password'],
                database=self.config['database'],
                charset='utf8mb4',
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
            )
            print("✓ Connexion à MariaDB établie")
        except Exception as e:
            print(f"❌ Erreur de connexion à MariaDB: {e}")
            raise

    def close(self):
        if self._connection:
            self._connection.close()
            print("🔌 Connexion fermée")

    def execute(self, sql: str, params=None) -> int:
        self._connection.ping(reconnect=True)
        with self._connection.cursor() as c:
            c.execute(sql, params or ())
            return c.lastrowid

    def query(self, sql: str, params=None) -> list:
        self._connection.ping(reconnect=True)
        with self._connection.cursor() as c:
            c.execute(sql, params or ())
            return c.fetchall()

    def column_exists(self, table: str, column: str) -> bool:
        rows = self.query(
            "SELECT COUNT(*) as cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (self.config['database'], table, column)
        )
        return rows[0]['cnt'] > 0

    def fmt_month(self, col: str) -> str:
        return f"DATE_FORMAT({col}, '%%Y-%%m')"

    def fmt_week(self, col: str) -> str:
        return f"CONCAT(YEAR({col}), '_', LPAD(WEEK({col}), 2, '0'))"

    def init_schema(self):
        try:
            self.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    enseigne VARCHAR(50) NOT NULL DEFAULT 'systeme_u',
                    date DATE NOT NULL,
                    heure TIME NOT NULL,
                    magasin VARCHAR(255) NOT NULL,
                    operateur VARCHAR(50),
                    tpv VARCHAR(20),
                    numero_ticket VARCHAR(50) NOT NULL,
                    total DECIMAL(10,2) NOT NULL,
                    mode_paiement VARCHAR(100),
                    fichier VARCHAR(255) UNIQUE NOT NULL,
                    parser_name VARCHAR(50) DEFAULT 'inconnu',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_date (date),
                    INDEX idx_ticket (numero_ticket),
                    INDEX idx_fichier (fichier),
                    INDEX idx_enseigne (enseigne)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            self.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    ticket_id INT NOT NULL,
                    nom VARCHAR(500) NOT NULL,
                    prix_unitaire DECIMAL(10,2) NOT NULL,
                    quantite DECIMAL(10,3) DEFAULT 1,
                    prix_total DECIMAL(10,2) NOT NULL,
                    tva_code VARCHAR(5),
                    rayon VARCHAR(255),
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
                    INDEX idx_ticket_id (ticket_id),
                    INDEX idx_nom (nom(100)),
                    INDEX idx_prix (prix_total),
                    INDEX idx_rayon (rayon)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            # Table des fichiers PDF (archivage blob + déduplication par hash)
            self.execute('''
                CREATE TABLE IF NOT EXISTS fichiers_pdf (
                    pdf_hash CHAR(64) PRIMARY KEY,
                    contenu LONGBLOB NOT NULL,
                    nom_origine VARCHAR(255) NOT NULL,
                    taille INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')
            # Migration : quantite INT → DECIMAL (anciens schémas)
            self.execute('ALTER TABLE articles MODIFY COLUMN quantite DECIMAL(10,3) DEFAULT 1')
            # Migrations colonnes ajoutées progressivement
            for col, alter_sql, extra in [
                ('enseigne',
                 "ALTER TABLE tickets ADD COLUMN enseigne VARCHAR(50) NOT NULL DEFAULT 'systeme_u' AFTER id",
                 "CREATE INDEX idx_enseigne ON tickets (enseigne)"),
                ('parser_name',
                 "ALTER TABLE tickets ADD COLUMN parser_name VARCHAR(50) DEFAULT 'inconnu'",
                 None),
                ('pdf_hash',
                 "ALTER TABLE tickets ADD COLUMN pdf_hash CHAR(64) NULL REFERENCES fichiers_pdf(pdf_hash)",
                 "CREATE INDEX idx_pdf_hash ON tickets (pdf_hash)"),
            ]:
                if not self.column_exists('tickets', col):
                    self.execute(alter_sql)
                    if extra:
                        self.execute(extra)
            print("✓ Tables MariaDB initialisées")
        except Exception as e:
            print(f"❌ Erreur initialisation tables: {e}")
            raise


# ─── SQLite ───────────────────────────────────────────────────────────────────

class SQLiteDatabase(BaseDatabase):

    def connect(self):
        db_path = self.config.get('path', 'tickets.db')
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        # Support de REGEXP via fonction Python
        self._connection.create_function('regexp', 2,
            lambda p, s: bool(re.search(p, s or '')))
        self._connection.execute("PRAGMA foreign_keys = ON")
        print(f"✓ Base SQLite ouverte : {db_path}")

    def close(self):
        if self._connection:
            self._connection.close()
            print("🔌 Connexion fermée")

    def execute(self, sql: str, params=None) -> int:
        cursor = self._connection.execute(self._adapt(sql), params or ())
        self._connection.commit()
        return cursor.lastrowid

    def query(self, sql: str, params=None) -> list:
        cursor = self._connection.execute(self._adapt(sql), params or ())
        return [dict(row) for row in cursor.fetchall()]

    def column_exists(self, table: str, column: str) -> bool:
        rows = self.query(f"PRAGMA table_info({table})")
        return any(row['name'] == column for row in rows)

    def fmt_month(self, col: str) -> str:
        return f"strftime('%Y-%m', {col})"

    def fmt_week(self, col: str) -> str:
        return f"strftime('%Y_%W', {col})"

    def _adapt(self, sql: str) -> str:
        """Convertit les placeholders %s → ? pour sqlite3"""
        return sql.replace('%s', '?')

    def init_schema(self):
        try:
            self.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY,
                    enseigne TEXT NOT NULL DEFAULT 'systeme_u',
                    date DATE NOT NULL,
                    heure TEXT NOT NULL,
                    magasin TEXT NOT NULL,
                    operateur TEXT,
                    tpv TEXT,
                    numero_ticket TEXT NOT NULL,
                    total REAL NOT NULL,
                    mode_paiement TEXT,
                    fichier TEXT UNIQUE NOT NULL,
                    parser_name TEXT DEFAULT 'inconnu',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY,
                    ticket_id INTEGER NOT NULL,
                    nom TEXT NOT NULL,
                    prix_unitaire REAL NOT NULL,
                    quantite REAL DEFAULT 1,
                    prix_total REAL NOT NULL,
                    tva_code TEXT,
                    rayon TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE
                )
            ''')
            # Table des fichiers PDF (archivage blob + déduplication par hash)
            self.execute('''
                CREATE TABLE IF NOT EXISTS fichiers_pdf (
                    pdf_hash TEXT PRIMARY KEY,
                    contenu BLOB NOT NULL,
                    nom_origine TEXT NOT NULL,
                    taille INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            for idx in [
                "CREATE INDEX IF NOT EXISTS idx_date       ON tickets (date)",
                "CREATE INDEX IF NOT EXISTS idx_ticket     ON tickets (numero_ticket)",
                "CREATE INDEX IF NOT EXISTS idx_fichier    ON tickets (fichier)",
                "CREATE INDEX IF NOT EXISTS idx_enseigne   ON tickets (enseigne)",
                "CREATE INDEX IF NOT EXISTS idx_ticket_id  ON articles (ticket_id)",
                "CREATE INDEX IF NOT EXISTS idx_nom        ON articles (nom)",
                "CREATE INDEX IF NOT EXISTS idx_prix       ON articles (prix_total)",
                "CREATE INDEX IF NOT EXISTS idx_rayon      ON articles (rayon)",
                "CREATE INDEX IF NOT EXISTS idx_pdf_hash   ON tickets (pdf_hash)",
            ]:
                self.execute(idx)
            # Migrations colonnes
            for col, alter_sql in [
                ('enseigne',    "ALTER TABLE tickets ADD COLUMN enseigne TEXT NOT NULL DEFAULT 'systeme_u'"),
                ('parser_name', "ALTER TABLE tickets ADD COLUMN parser_name TEXT DEFAULT 'inconnu'"),
                ('pdf_hash',    "ALTER TABLE tickets ADD COLUMN pdf_hash TEXT NULL"),
            ]:
                if not self.column_exists('tickets', col):
                    self.execute(alter_sql)
            print("✓ Tables SQLite initialisées")
        except Exception as e:
            print(f"❌ Erreur initialisation tables: {e}")
            raise
