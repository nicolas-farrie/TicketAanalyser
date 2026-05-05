#!/usr/bin/env python3
"""
Analyseur de tickets de caisse PDF Système U
Extrait les informations des tickets et les stocke en base MariaDB
Optimisé pour le format des tickets Système U / LOCOMA SAS

https://claude.ai/chat/64d8e228-2365-4c99-8e91-d108a2091d3d
"""

import argparse
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

import PyPDF2
import pdfplumber
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from database import create_database, load_config

# Vérification de la disponibilité de Tkinter
try:
    import tkinter as tk
    from tkinter import simpledialog, messagebox
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

@dataclass
class Article:
    nom: str
    prix_unitaire: float
    quantite: float  # float pour supporter les articles au poids (ex: 0,697 kg)
    prix_total: float
    tva_code: str
    rayon: Optional[str] = None


@dataclass
class Ticket:
    enseigne: str
    date: datetime
    heure: str
    magasin: str
    operateur: str
    tpv: str
    numero_ticket: str
    articles: List[Article]
    total: float
    mode_paiement: str
    fichier: str
    parser_name: str
    pdf_hash: Optional[str] = None


class BaseTicketParser(ABC):
    """Classe de base pour les parsers de tickets"""

    # Identifiant unique de l'enseigne (ex: "systeme_u", "carrefour", "leclerc")
    enseigne: str = "inconnu"
    # Nom complet de l'enseigne pour affichage
    enseigne_nom: str = "Enseigne inconnue"
    # Nom du parser en fonction du modèle de ticket
    parser_name: str = "inconnu"

    def get_nom_magasin(self, lignes: List[str]) -> str:
        """Retourne le nom du magasin. Peut être surchargé pour extraire du ticket."""
        return self.enseigne_nom

    @abstractmethod
    def detecter_format(self, lignes: List[str]) -> bool:
        """Retourne True si ce parser peut traiter ce format"""
        pass

    @abstractmethod
    def extraire_info_entete(self, lignes: List[str]) -> Optional[dict]:
        """Extrait date, heure, operateur, tpv, numero_ticket"""
        pass

    @abstractmethod
    def extraire_articles(self, lignes: List[str]) -> List[Article]:
        """Extrait la liste des articles"""
        pass

    @abstractmethod
    def extraire_paiement(self, lignes: List[str]) -> tuple:
        """Extrait le total et le mode de paiement"""
        pass


class AncienFormatParser(BaseTicketParser):
    """Parser pour l'ancien format (avant octobre 2025)

    Caractéristiques :
    - Header en haut : "Opérateur Date Heure TPV Ticket"
    - Articles : "NOM PRIX € TVA" ou multi-lignes avec "N x PRIX €"
    - Fin articles : "=========="
    """

    enseigne = "systeme_u"
    enseigne_nom = "Système U"
    parser_name = "format_1"

    def get_nom_magasin(self, lignes: List[str]) -> str:
        """Extrait le nom du magasin depuis le ticket (LOCOMA SAS)"""
        for ligne in lignes[:15]:
            if 'LOCOMA' in ligne:
                return "LOCOMA SAS - Système U Lodève"
        return self.enseigne_nom

    def detecter_format(self, lignes: List[str]) -> bool:
        """Détecte l'ancien format via la présence de 'Opérateur Date Heure TPV Ticket'"""
        for ligne in lignes[:20]:
            if 'Opérateur' in ligne and 'Date' in ligne and 'Heure' in ligne:
                return True
        return False

    def extraire_info_entete(self, lignes: List[str]) -> Optional[dict]:
        """Extrait les informations d'en-tête du ticket Système U (ancien format)"""
        # Pattern: "Opérateur Date Heure TPV Ticket"
        # Exemple: "902 SCO2 26/09/25 17:18 102 885149"

        for ligne in lignes:
            match = re.match(r'(\w+)\s+(\w+)\s+(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})\s+(\w+)\s+(\d+)', ligne)
            if match:
                operateur_num, operateur_code, date_str, heure_str, tpv, ticket = match.groups()

                try:
                    # Conversion de la date (format DD/MM/YY)
                    date_obj = datetime.strptime(date_str, '%d/%m/%y')
                    return {
                        'operateur': f"{operateur_num} {operateur_code}",
                        'date': date_obj.date(),
                        'heure': heure_str,
                        'tpv': tpv,
                        'ticket': ticket
                    }
                except ValueError:
                    continue

        return None

    def extraire_articles(self, lignes: List[str]) -> List[Article]:
        """Extrait les articles du ticket Système U (ancien format)

        Gère 4 formats :
        1. Article simple : "NOM 5.81 € 11"
        2. Article + détail : "NOM 15.98 € 11" suivi de "2 x 7,99 € 15,98 € 11"
        3. Article 2 lignes : "NOM" suivi de "2 x 7,99 € 15,98 € 11"
        4. Remises : "DESCRIPTION -5.99 €" (prix négatif)
        """
        articles = []
        rayon_actuel = None
        i = 0

        while i < len(lignes):
            ligne = lignes[i]

            # Détection des rayons (commencent par >>>>)
            if ligne.startswith('>>>>'):
                rayon_actuel = ligne.replace('>>>>', '').strip()
                i += 1
                continue

            # Ignorer les lignes de totaux et sous-totaux
            if any(keyword in ligne for keyword in ['SOUS TOTAL', 'REMISE TOTALE', 'TOTAL', '====']):
                i += 1
                continue

            # CAS 1: Ligne de multiplicateur seule (à ignorer car déjà traitée)
            # Format: "2 x 7,99 € 15,98 € 11"
            if re.match(r'^\s*\d+\s+x\s+\d+[,.]\d{2}\s*€', ligne):
                i += 1
                continue

            # CAS 2: Remise (prix négatif)
            # Format: "5.99E/LOT 3 TERREAU U 50L -5,99 €"
            match_remise = re.match(r'^(.+?)\s+(-\d+[,.]\d{2})\s*€\s*$', ligne)
            if match_remise:
                nom, prix_str = match_remise.groups()
                nom = nom.strip()
                prix_total = float(prix_str.replace(',', '.'))

                articles.append(Article(
                    nom=f"REMISE: {nom}",
                    prix_unitaire=prix_total,
                    quantite=1,
                    prix_total=prix_total,
                    tva_code="00",
                    rayon=rayon_actuel
                ))
                i += 1
                continue

            # CAS 3: Ligne article standard avec prix et TVA sur la même ligne
            # Format: "MAGNUM CLASS.ALM.WHIT CHOC.X8 5.81 € 11"
            match_std = re.match(r'^(.+?)\s+(\d+[,.]\d{2})\s*€\s+(\d+)$', ligne)
            if match_std:
                nom, prix_str, tva = match_std.groups()
                nom = nom.strip()
                prix_total = float(prix_str.replace(',', '.'))

                # Vérifier si quantité dans le nom (X8, etc.)
                qte_match = re.search(r'[xX](\d+)$', nom)
                if qte_match:
                    quantite = int(qte_match.group(1))
                    nom = re.sub(r'\s*[xX]\d+$', '', nom).strip()
                    prix_unitaire = prix_total / quantite
                else:
                    quantite = 1
                    prix_unitaire = prix_total

                # IMPORTANT: Vérifier si la ligne suivante est un multiplicateur
                # Format: "2 x 7,99 € 15,98 € 11"
                if i + 1 < len(lignes):
                    ligne_suivante = lignes[i + 1]
                    match_detail = re.match(r'^(\d+)\s+x\s+(\d+[,.]\d{2})\s*€\s+(\d+[,.]\d{2})\s*€\s+(\d+)$',
                                            ligne_suivante)
                    if match_detail:
                        qte_detail, prix_unit_detail, prix_total_detail, tva_detail = match_detail.groups()
                        # Utiliser les détails de la ligne suivante
                        quantite = int(qte_detail)
                        prix_unitaire = float(prix_unit_detail.replace(',', '.'))
                        prix_total = float(prix_total_detail.replace(',', '.'))
                        tva = tva_detail
                        i += 1  # Skip la ligne suivante car on l'a traitée

                # Créer l'article
                articles.append(Article(
                    nom=nom,
                    prix_unitaire=prix_unitaire,
                    quantite=quantite,
                    prix_total=prix_total,
                    tva_code=tva,
                    rayon=rayon_actuel
                ))

            # CAS 4: Ligne sans prix (nom d'article seul sur une ligne)
            # La ligne suivante devrait contenir les détails
            elif i + 1 < len(lignes):
                ligne_suivante = lignes[i + 1]
                # Vérifier si la ligne suivante est un multiplicateur complet
                match_multi = re.match(r'^(\d+)\s+x\s+(\d+[,.]\d{2})\s*€\s+(\d+[,.]\d{2})\s*€\s+(\d+)$',
                                       ligne_suivante)
                if match_multi:
                    nom = ligne.strip()
                    qte_str, prix_unit_str, prix_total_str, tva = match_multi.groups()

                    quantite = int(qte_str)
                    prix_unitaire = float(prix_unit_str.replace(',', '.'))
                    prix_total = float(prix_total_str.replace(',', '.'))

                    articles.append(Article(
                        nom=nom,
                        prix_unitaire=prix_unitaire,
                        quantite=quantite,
                        prix_total=prix_total,
                        tva_code=tva,
                        rayon=rayon_actuel
                    ))

                    i += 1  # Skip la ligne suivante car traitée

            i += 1

        return articles

    def extraire_paiement(self, lignes: List[str]) -> tuple:
        """Extrait le total et le mode de paiement (ancien format)"""
        total = 0.0
        mode_paiement = "Non spécifié"

        for ligne in lignes:
            # Total
            if ligne.startswith('TOTAL TVA') and '€' in ligne:
                match = re.search(r'(\d+[,.]\d{2})\s*€\s*$', ligne)
                if match:
                    total = float(match.group(1).replace(',', '.'))

            # Mode de paiement
            elif any(paiement in ligne for paiement in ['CARTE BANCAIRE', 'CB SANS CONTACT', 'ESPECES', 'CHEQUE']):
                if 'CARTE BANCAIRE' in ligne or 'CB SANS CONTACT' in ligne:
                    mode_paiement = "Carte bancaire"
                elif 'ESPECES' in ligne:
                    mode_paiement = "Espèces"
                elif 'CHEQUE' in ligne:
                    mode_paiement = "Chèque"

        return total, mode_paiement


class NouveauFormatParser(BaseTicketParser):
    """Parser pour le nouveau format (à partir d'octobre 2025)

    Caractéristiques :
    - Header en bas : "Date Heure Magasin Tpv Util Tick"
    - Début articles : "*** VENTE ***"
    - Catégories en majuscules (EPICES, FRUITS, etc.)
    - Articles au poids : "Pesée manuelle" + "X,XXX kg x PRIX €/kg"
    - Fin articles : "TOTAL [N] Article"
    """

    enseigne = "systeme_u"
    enseigne_nom = "Système U"
    parser_name = "format_2"

    def get_nom_magasin(self, lignes: List[str]) -> str:
        """Extrait le nom du magasin depuis le ticket"""
        for ligne in lignes[:15]:
            if 'LOCOMA' in ligne:
                return "LOCOMA SAS - Système U Lodève"
        return self.enseigne_nom

    def detecter_format(self, lignes: List[str]) -> bool:
        """Détecte le nouveau format via la présence de '*** VENTE ***'"""
        return any('*** VENTE ***' in ligne for ligne in lignes)

    def extraire_info_entete(self, lignes: List[str]) -> Optional[dict]:
        """Extrait les informations d'en-tête (en bas du ticket)

        Format:
        Date Heure Magasin Tpv Util Tick
        02/11/25 12:12:10 90423 061 200 3434
        """
        for i, ligne in enumerate(lignes):
            if 'Date' in ligne and 'Heure' in ligne and 'Magasin' in ligne:
                # La ligne suivante contient les valeurs
                if i + 1 < len(lignes):
                    ligne_valeurs = lignes[i + 1]
                    match = re.match(
                        r'(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',
                        ligne_valeurs
                    )
                    if match:
                        date_str, heure_str, magasin, tpv, utilisateur, ticket = match.groups()
                        try:
                            date_obj = datetime.strptime(date_str, '%d/%m/%y')
                            return {
                                'operateur': utilisateur,
                                'date': date_obj.date(),
                                'heure': heure_str[:5],  # Format HH:MM
                                'tpv': tpv,
                                'ticket': ticket
                            }
                        except ValueError:
                            continue
        return None

    def extraire_articles(self, lignes: List[str]) -> List[Article]:
        """Extrait les articles du ticket (nouveau format)

        Gère :
        - Articles standards : "NOM PRIX € TVA" + "1 x PRIX EUR"
        - Articles au poids : "NOM PRIX € TVA" + "Pesée manuelle" + "X kg x PRIX €/kg"
        - Catégories : lignes en majuscules sans prix
        """
        articles = []
        rayon_actuel = None
        i = 0

        # Trouver le début et la fin de la zone articles
        debut_articles = None
        fin_articles = None

        for idx, ligne in enumerate(lignes):
            if '*** VENTE ***' in ligne:
                debut_articles = idx + 1
            elif re.match(r'^TOTAL\s*\[\d+\]\s*Article', ligne):
                fin_articles = idx
                break

        if debut_articles is None:
            return articles

        if fin_articles is None:
            fin_articles = len(lignes)

        # Parser les lignes dans la zone articles
        i = debut_articles
        while i < fin_articles:
            ligne = lignes[i]

            # Ignorer les lignes vides ou avec tirets
            if not ligne or ligne.startswith('---'):
                i += 1
                continue

            # Ignorer les lignes de quantité seules (déjà traitées)
            if re.match(r'^\d+\s+x\s+\d+[,.]\d{2}\s+EUR$', ligne):
                i += 1
                continue

            # Ignorer "Pesée manuelle" seul (déjà traité)
            if ligne == 'Pesée manuelle':
                i += 1
                continue

            # Ignorer les lignes de pesée seules (déjà traitées)
            if re.match(r'^\d+[,.]\d+\s+kg\s+x\s+\d+[,.]\d{2}\s*€/kg$', ligne):
                i += 1
                continue

            # Détection de catégorie (ligne en majuscules sans prix, pas TOTAL/SOUS-TOTAL)
            if self._est_categorie(ligne):
                rayon_actuel = ligne.strip()
                i += 1
                continue

            # Article avec prix et TVA
            match_article = re.match(r'^(.+?)\s+(\d+[,.]\d{2})\s*€\s+(\d+)$', ligne)
            if match_article:
                nom, prix_str, tva = match_article.groups()
                nom = nom.strip()
                prix_total = float(prix_str.replace(',', '.'))
                quantite = 1.0
                prix_unitaire = prix_total

                # Vérifier les lignes suivantes pour détails
                if i + 1 < fin_articles:
                    ligne_suivante = lignes[i + 1]

                    # Cas pesée manuelle
                    if ligne_suivante == 'Pesée manuelle' and i + 2 < fin_articles:
                        ligne_pesee = lignes[i + 2]
                        match_pesee = re.match(r'^(\d+[,.]\d+)\s+kg\s+x\s+(\d+[,.]\d{2})\s*€/kg$', ligne_pesee)
                        if match_pesee:
                            poids_str, prix_kg_str = match_pesee.groups()
                            quantite = float(poids_str.replace(',', '.'))
                            prix_unitaire = float(prix_kg_str.replace(',', '.'))
                            i += 2  # Skip les deux lignes suivantes

                    # Cas quantité standard "N x PRIX EUR"
                    else:
                        match_qte = re.match(r'^(\d+)\s+x\s+(\d+[,.]\d{2})\s+EUR$', ligne_suivante)
                        if match_qte:
                            qte_str, prix_unit_str = match_qte.groups()
                            quantite = float(qte_str)
                            prix_unitaire = float(prix_unit_str.replace(',', '.'))
                            i += 1  # Skip la ligne suivante

                articles.append(Article(
                    nom=nom,
                    prix_unitaire=prix_unitaire,
                    quantite=quantite,
                    prix_total=prix_total,
                    tva_code=tva,
                    rayon=rayon_actuel
                ))

            i += 1

        return articles

    def _est_categorie(self, ligne: str) -> bool:
        """Détermine si une ligne est une catégorie (rayon)"""
        ligne = ligne.strip()
        if not ligne:
            return False

        # Doit être en majuscules
        if ligne != ligne.upper():
            return False

        # Ne doit pas contenir de prix
        if '€' in ligne or 'EUR' in ligne:
            return False

        # Ne doit pas être un mot-clé système
        mots_exclus = ['TOTAL', 'SOUS-TOTAL', 'REMISE', 'VENTE', 'CB', 'CARTE']
        for mot in mots_exclus:
            if mot in ligne:
                return False

        # Doit être composé de lettres, espaces, tirets, parenthèses, points
        if re.match(r'^[A-Z][A-Z\s.\-()]+$', ligne):
            return True

        return False

    def extraire_paiement(self, lignes: List[str]) -> tuple:
        """Extrait le total et le mode de paiement (nouveau format)"""
        total = 0.0
        mode_paiement = "Non spécifié"

        for ligne in lignes:
            # Total avec format "TOTAL [N] Articles XX,XX €"
            match_total = re.match(r'^TOTAL\s*\[\d+\]\s*Article.*?\s+(\d+[,.]\d{2})\s*€', ligne)
            if match_total:
                total = float(match_total.group(1).replace(',', '.'))

            # Mode de paiement
            if any(paiement in ligne for paiement in ['CARTE BANCAIRE', 'CB SANS CONTACT', 'ESPECES', 'CHEQUE']):
                if 'CARTE BANCAIRE' in ligne or 'CB SANS CONTACT' in ligne:
                    mode_paiement = "Carte bancaire"
                elif 'ESPECES' in ligne:
                    mode_paiement = "Espèces"
                elif 'CHEQUE' in ligne:
                    mode_paiement = "Chèque"

        return total, mode_paiement


class AnalyseurTicketU:
    def __init__(self, config_file: str = "config.ini", dry_run: bool = False):
        self.dry_run = dry_run
        self.config = load_config(config_file)
        self.db = create_database(self.config)
        # État de session pour la gestion des doublons (None / 'all' / 'none')
        on_dup = self.config.get('on_duplicate', 'ask').lower()
        self.update_mode: Optional[str] = {'skip': 'none', 'update': 'all'}.get(on_dup)
        self.parsers: List[BaseTicketParser] = [
            NouveauFormatParser(),
            AncienFormatParser(),
        ]

    def _stocker_pdf(self, chemin_pdf: str) -> str:
        """Lit le PDF, calcule son SHA-256, stocke le blob si nouveau. Retourne le hash."""
        with open(chemin_pdf, 'rb') as f:
            contenu = f.read()
        pdf_hash = hashlib.sha256(contenu).hexdigest()
        rows = self.db.query("SELECT pdf_hash FROM fichiers_pdf WHERE pdf_hash = %s", (pdf_hash,))
        if not rows:
            self.db.execute(
                "INSERT INTO fichiers_pdf (pdf_hash, contenu, nom_origine, taille) VALUES (%s, %s, %s, %s)",
                (pdf_hash, contenu, os.path.basename(chemin_pdf), len(contenu))
            )
        return pdf_hash

    def extraire_texte_pdf(self, chemin_pdf: str) -> str:
        """Extrait le texte d'un PDF avec fallback automatique"""
        try:
            # Méthode principale avec pdfplumber
            with pdfplumber.open(chemin_pdf) as pdf:
                texte = ""
                for page in pdf.pages:
                    texte += page.extract_text() or ""
                if texte.strip():
                    return texte
        except Exception as e:
            print(f"  ⚠ Erreur pdfplumber: {e}")

        try:
            # Fallback avec PyPDF2
            with open(chemin_pdf, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                texte = ""
                for page in reader.pages:
                    texte += page.extract_text()
                return texte
        except Exception as e:
            print(f"  ⚠ Erreur PyPDF2: {e}")
            return ""

    def parser_ticket(self, texte: str, nom_fichier: str, pdf_hash: Optional[str] = None) -> Optional[Ticket]:
        """Parse le contenu d'un ticket Système U avec sélection automatique du parser"""
        lignes = [ligne.strip() for ligne in texte.split('\n') if ligne.strip()]

        # Sélection automatique du parser
        parser = self._selectionner_parser(lignes)
        if not parser:
            print("  ⚠ Format de ticket non reconnu")
            return None

        # Extraction avec le parser approprié
        info_ticket = parser.extraire_info_entete(lignes)
        if not info_ticket:
            print("  ⚠ Impossible d'extraire les informations d'en-tête")
            return None

        articles = parser.extraire_articles(lignes)
        if not articles:
            print("  ⚠ Aucun article trouvé")
            return None

        total, mode_paiement = parser.extraire_paiement(lignes)

        return Ticket(
            enseigne=parser.enseigne,
            date=info_ticket['date'],
            heure=info_ticket['heure'],
            magasin=parser.get_nom_magasin(lignes),
            operateur=info_ticket['operateur'],
            tpv=info_ticket['tpv'],
            numero_ticket=info_ticket['ticket'],
            articles=articles,
            total=total,
            mode_paiement=mode_paiement,
            fichier=nom_fichier,
            parser_name=parser.parser_name,
            pdf_hash=pdf_hash,
        )

    def _selectionner_parser(self, lignes: List[str]) -> Optional[BaseTicketParser]:
        """Sélectionne automatiquement le parser approprié pour le format du ticket"""
        for parser in self.parsers:
            if parser.detecter_format(lignes):
                return parser
        return None

    def sauvegarder_ticket(self, ticket: Ticket):
        """Sauvegarde un ticket en base (no-op en mode dry-run)"""
        if self.dry_run:
            print(f"  [DRY-RUN] {ticket.fichier} — {len(ticket.articles)} articles, {ticket.total:.2f}€ ({ticket.parser_name})")
            return

        try:
            # Déduplication : hash OU fichier (les anciens tickets ont pdf_hash=NULL)
            if ticket.pdf_hash:
                rows = self.db.query(
                    "SELECT id FROM tickets WHERE pdf_hash = %s OR fichier = %s",
                    (ticket.pdf_hash, ticket.fichier)
                )
            else:
                rows = self.db.query("SELECT id FROM tickets WHERE fichier = %s", (ticket.fichier,))
            existing = rows[0] if rows else None

            if existing:
                ticket_id = existing['id']

                if self.update_mode == 'none':
                    print(f"  - Ticket {ticket.fichier} ignoré (mode Aucun)")
                    return
                elif self.update_mode == 'all':
                    do_update = True
                else:
                    print(f"  - Ticket {ticket.fichier} déjà en base. Mettre à jour ?")
                    reponse = ""
                    while not reponse:
                        reponse = input("-- [O]ui / [N]on / [T]ous / [A]ucun : ").strip()
                    r = reponse[0].upper()
                    if r == 'O':
                        do_update = True
                    elif r == 'N':
                        print(f"  - Ignoré")
                        return
                    elif r == 'T':
                        do_update = True
                        self.update_mode = 'all'
                        print(f"  - Mode 'Tous' activé pour cette session")
                    elif r == 'A':
                        self.update_mode = 'none'
                        print(f"  - Mode 'Aucun' activé, ticket ignoré")
                        return
                    else:
                        print(f"  - Réponse non reconnue, ticket ignoré")
                        return

                if do_update:
                    self.db.execute('''
                        UPDATE tickets SET enseigne=%s, date=%s, heure=%s, magasin=%s,
                            operateur=%s, tpv=%s, numero_ticket=%s, total=%s,
                            mode_paiement=%s, parser_name=%s, pdf_hash=%s
                        WHERE id=%s
                    ''', (ticket.enseigne, ticket.date, ticket.heure, ticket.magasin,
                          ticket.operateur, ticket.tpv, ticket.numero_ticket, ticket.total,
                          ticket.mode_paiement, ticket.parser_name, ticket.pdf_hash, ticket_id))
                    self.db.execute("DELETE FROM articles WHERE ticket_id = %s", (ticket_id,))
                    for article in ticket.articles:
                        self.db.execute('''
                            INSERT INTO articles (ticket_id, nom, prix_unitaire, quantite, prix_total, tva_code, rayon)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ''', (ticket_id, article.nom, article.prix_unitaire, article.quantite,
                              article.prix_total, article.tva_code, article.rayon))
                    print(f"  ✓ Ticket {ticket.fichier} mis à jour: {len(ticket.articles)} articles, {ticket.total:.2f}€")
                return

            # Nouveau ticket : insertion
            ticket_id = self.db.execute('''
                INSERT INTO tickets (enseigne, date, heure, magasin, operateur, tpv, numero_ticket,
                                     total, mode_paiement, fichier, parser_name, pdf_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (ticket.enseigne, ticket.date, ticket.heure, ticket.magasin, ticket.operateur,
                  ticket.tpv, ticket.numero_ticket, ticket.total, ticket.mode_paiement,
                  ticket.fichier, ticket.parser_name, ticket.pdf_hash))

            for article in ticket.articles:
                self.db.execute('''
                    INSERT INTO articles (ticket_id, nom, prix_unitaire, quantite, prix_total, tva_code, rayon)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (ticket_id, article.nom, article.prix_unitaire, article.quantite,
                      article.prix_total, article.tva_code, article.rayon))

            print(f"  ✓ Ticket {ticket.fichier} sauvegardé: {len(ticket.articles)} articles, {ticket.total:.2f}€")

        except Exception as e:
            print(f"  ❌ Erreur sauvegarde {ticket.fichier}: {e}")

    def traiter_dossier(self, chemin_dossier: str):
        """Traite tous les PDFs d'un dossier"""
        dossier = Path(chemin_dossier)
        fichiers_pdf = list(dossier.glob("*.pdf"))

        if not fichiers_pdf:
            print("❌ Aucun fichier PDF trouvé dans le dossier")
            return

        self.update_mode = None  # Réinitialiser l'état de session à chaque batch
        mode_label = " [DRY-RUN — aucune écriture en base]" if self.dry_run else ""
        print(f"\n=== TRAITEMENT DE {len(fichiers_pdf)} FICHIERS PDF{mode_label} ===")

        tickets_traites = 0
        tickets_erreur = 0

        for i, fichier_pdf in enumerate(fichiers_pdf, 1):
            print(f"\n[{i:3d}/{len(fichiers_pdf)}] {fichier_pdf.name}")

            pdf_hash = self._stocker_pdf(str(fichier_pdf)) if not self.dry_run else None

            texte = self.extraire_texte_pdf(str(fichier_pdf))
            if not texte.strip():
                print("  ❌ Pas de texte extrait")
                tickets_erreur += 1
                continue

            ticket = self.parser_ticket(texte, fichier_pdf.name, pdf_hash)
            if ticket:
                self.sauvegarder_ticket(ticket)
                tickets_traites += 1
            else:
                print("  ❌ Impossible de parser le ticket")
                tickets_erreur += 1

        print(f"\n=== RÉSUMÉ DU TRAITEMENT ===")
        print(f"✓ Tickets traités avec succès: {tickets_traites}")
        print(f"❌ Tickets en erreur: {tickets_erreur}")
        print(f"📊 Taux de réussite: {tickets_traites / (tickets_traites + tickets_erreur) * 100:.1f}%" if (
                                                                                                                      tickets_traites + tickets_erreur) > 0 else "")

    def verification_tickets(self):
        """Fonction de vérification pour identifier les problèmes de parsing"""
        try:
            print(f"\n=== VÉRIFICATION DES DONNÉES ===")

            tickets_probleme = self.db.query('''
                SELECT t.id, t.numero_ticket, t.fichier, t.total as total_ticket,
                       SUM(a.prix_total) as total_articles,
                       ABS(t.total - SUM(a.prix_total)) as ecart
                FROM tickets t
                JOIN articles a ON t.id = a.ticket_id
                GROUP BY t.id
                HAVING ABS(t.total - SUM(a.prix_total)) > 0.01
                ORDER BY ecart DESC
                LIMIT 10
            ''')
            if tickets_probleme:
                print(f"\n⚠️  {len(tickets_probleme)} ticket(s) avec écart de total:")
                for row in tickets_probleme:
                    print(f"  • Ticket {row['numero_ticket']} ({row['fichier']}): "
                          f"{row['total_ticket']:.2f}€ vs {row['total_articles']:.2f}€ "
                          f"(écart: {row['ecart']:.2f}€)")
            else:
                print("✅ Tous les totaux sont cohérents")

            suspects = self.db.query('''
                SELECT COUNT(*) as nb FROM articles
                WHERE nom REGEXP '^[0-9]+ x [0-9]+[,.][0-9]+.*€.*$'
            ''')
            nb_suspects = suspects[0]['nb']
            if nb_suspects > 0:
                print(f"\n⚠️  {nb_suspects} article(s) suspect(s) (lignes de multiplication mal parsées)")
                for row in self.db.query('''
                    SELECT id, ticket_id, nom, prix_total FROM articles
                    WHERE nom REGEXP '^[0-9]+ x [0-9]+[,.][0-9]+.*€.*$'
                    LIMIT 5
                '''):
                    print(f"  • ID {row['id']}: '{row['nom']}' - {row['prix_total']:.2f}€")
            else:
                print("✅ Aucun article suspect détecté")

            nb_tickets  = self.db.query("SELECT COUNT(*) as nb FROM tickets")[0]['nb']
            nb_articles = self.db.query("SELECT COUNT(*) as nb FROM articles")[0]['nb']
            total_global = self.db.query("SELECT COALESCE(SUM(total), 0) as total FROM tickets")[0]['total']

            print(f"\n📊 STATISTIQUES GÉNÉRALES")
            print(f"  • Tickets en base: {nb_tickets}")
            print(f"  • Articles en base: {nb_articles}")
            print(f"  • Montant total: {total_global:.2f}€")
            if nb_tickets > 0:
                print(f"  • Moyenne par ticket: {total_global / nb_tickets:.2f}€")

        except Exception as e:
            print(f"❌ Erreur lors de la vérification: {e}")

    def statistiques(self):
        """Affiche des statistiques détaillées"""
        try:
            print(f"\n=== ANALYSES DÉTAILLÉES ===")

            r = self.db.query(
                "SELECT MIN(date) as min_d, MAX(date) as max_d, COUNT(*) as nb, SUM(total) as total FROM tickets"
            )[0]
            if r['min_d']:
                print(f"📅 Période: {r['min_d']} au {r['max_d']}")
                print(f"🛒 {r['nb']} tickets pour {r['total']:.2f}€")
                print(f"💰 Moyenne par ticket: {r['total'] / r['nb']:.2f}€")

            print(f"\n🏆 TOP 15 ARTICLES (par montant total)")
            print(f"{'Produit':<45} {'Achats':<7} {'Qté':<5} {'Prix moy.':<10} {'Total'}")
            print("─" * 80)
            for row in self.db.query('''
                SELECT nom, COUNT(*) as nb_achats, SUM(quantite) as qte_totale,
                       AVG(prix_unitaire) as prix_moyen, SUM(prix_total) as total_depense
                FROM articles GROUP BY nom ORDER BY total_depense DESC LIMIT 15
            '''):
                nom_court = row['nom'][:42] + "..." if len(row['nom']) > 45 else row['nom']
                print(f"{nom_court:<45} {row['nb_achats']:<7} {row['qte_totale']:<5.3g} "
                      f"{row['prix_moyen']:<10.2f} {row['total_depense']:.2f}€")

            evolution = self.db.query(f'''
                SELECT {self.db.fmt_month('date')} as mois,
                       COUNT(*) as nb_tickets, SUM(total) as total_mois
                FROM tickets
                GROUP BY {self.db.fmt_month('date')}
                ORDER BY mois DESC LIMIT 12
            ''')
            if evolution:
                print(f"\n📈 ÉVOLUTION MENSUELLE (12 derniers mois)")
                print(f"{'Mois':<10} {'Tickets':<8} {'Montant':<12} {'Moyenne'}")
                print("─" * 45)
                for row in evolution:
                    moyenne = row['total_mois'] / row['nb_tickets'] if row['nb_tickets'] > 0 else 0
                    print(f"{row['mois']:<10} {row['nb_tickets']:<8} {row['total_mois']:<12.2f} {moyenne:.2f}€")

        except Exception as e:
            print(f"❌ Erreur lors des statistiques: {e}")

    def close(self):
        self.db.close()


def main():
    """Fonction principale"""
    parser = argparse.ArgumentParser(
        description="Analyseur de tickets de caisse PDF Système U",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples:\n"
            "  %(prog)s --path /chemin/vers/tickets/\n"
            "  %(prog)s --path /chemin/vers/tickets/ --dry-run\n"
            "  %(prog)s --config mon_config.ini\n"
        )
    )
    parser.add_argument(
        "--path", "-p",
        metavar="DOSSIER",
        help="Chemin vers le dossier contenant les fichiers PDF"
    )
    parser.add_argument(
        "--config", "-c",
        metavar="FICHIER",
        default="config.ini",
        help="Fichier de configuration (défaut: config.ini)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse les tickets sans écrire en base de données"
    )
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║        ANALYSEUR TICKETS SYSTÈME U       ║")
    print("║             Version 1.0                  ║")
    print("╚══════════════════════════════════════════╝")

    # Résoudre le chemin : arg CLI > config.ini > input interactif
    dossier_tickets = args.path
    if not dossier_tickets:
        cfg = load_config(args.config)
        dossier_tickets = cfg.get('dir_path', '').strip()
    if not dossier_tickets:
        print("\n📂 Configuration:")
        dossier_tickets = input("Chemin vers le dossier contenant les tickets PDF: ").strip()
    if not dossier_tickets:
        print("❌ Chemin vide, abandon.")
        return

    if not os.path.exists(dossier_tickets):
        print(f"❌ Dossier non trouvé : {dossier_tickets}")
        return

    analyseur = None

    try:
        analyseur = AnalyseurTicketU(config_file=args.config, dry_run=args.dry_run)

        analyseur.traiter_dossier(dossier_tickets)

        if not args.dry_run:
            analyseur.verification_tickets()
            analyseur.statistiques()

        print(f"\n✅ Traitement terminé!")
        if not args.dry_run:
            db_type = analyseur.config.get('type', 'mariadb')
            if db_type == 'sqlite':
                print(f"🗄️  Base SQLite : {analyseur.config.get('path', 'tickets.db')}")
            else:
                print(f"🗄️  Base MariaDB : {analyseur.config['database']} sur {analyseur.config['host']}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interruption par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur fatale: {e}")
    finally:
        if analyseur:
            analyseur.close()


if __name__ == "__main__":
    main()