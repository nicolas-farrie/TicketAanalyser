#!/usr/bin/env python3
"""
Dashboard web — Analyseur Tickets Système U
Visualisation des données extraites en base (MariaDB ou SQLite)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from database import create_database, load_config

# ─── Configuration page ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="Tickets Système U",
    page_icon="🧾",
    layout="wide",
)

# ─── Connexion BD ─────────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    config_file = os.environ.get("TICKET_CONFIG", "config.ini")
    return create_database(load_config(config_file))


db = get_db()

# ─── Analyse / mise à jour ────────────────────────────────────────────────────

_SCAN_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scan_state.json")
_SCAN_MIN_INTERVAL = 120  # secondes entre deux déclenchements automatiques


def _load_scan_state() -> dict:
    try:
        if os.path.exists(_SCAN_STATE_FILE):
            return json.loads(open(_SCAN_STATE_FILE).read())
    except Exception:
        pass
    return {"last_scan": 0}


def _save_scan_state(ts: float):
    try:
        with open(_SCAN_STATE_FILE, "w") as f:
            json.dump({"last_scan": ts}, f)
    except Exception:
        pass


def _new_pdfs_exist() -> bool:
    dir_path = db.config.get("dir_path", "")
    if not dir_path or not os.path.exists(dir_path):
        return False
    pdf_files = {p.name for p in Path(dir_path).glob("*.pdf")}
    if not pdf_files:
        return False
    try:
        processed = {row["fichier"] for row in db.query("SELECT fichier FROM tickets")}
        return bool(pdf_files - processed)
    except Exception:
        return True


def run_analyser(force: bool = False) -> tuple[bool, str]:
    """Lance l'analyseur si nécessaire. Retourne (lancé, journal)."""
    state = _load_scan_state()
    elapsed = time.time() - state.get("last_scan", 0)
    if not force:
        if elapsed < _SCAN_MIN_INTERVAL:
            return False, f"Scan récent ({int(elapsed)}s)"
        if not _new_pdfs_exist():
            _save_scan_state(time.time())
            return False, "Aucun nouveau PDF détecté"
    config_file = os.environ.get("TICKET_CONFIG", "config.ini")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_analyser.py")
    result = subprocess.run(
        [sys.executable, script, "--config", config_file],
        capture_output=True, text=True, timeout=300,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    _save_scan_state(time.time())
    output = result.stdout
    if result.returncode != 0 and result.stderr:
        output += f"\n[ERREUR]\n{result.stderr}"
    return True, output


# Auto-vérification à chaque nouvelle session (débouncée par _SCAN_MIN_INTERVAL)
if "auto_checked" not in st.session_state:
    st.session_state.auto_checked = True
    if run_analyser(force=False)[0]:
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────


def query(sql: str, params=None) -> pd.DataFrame:
    return pd.DataFrame(db.query(sql, params))


# ─── Sidebar : filtres ────────────────────────────────────────────────────────

st.sidebar.title("🔍 Filtres")

df_dates = query("SELECT MIN(date) as min_d, MAX(date) as max_d FROM tickets")
min_date = pd.to_datetime(df_dates["min_d"][0]).date() if not df_dates.empty and pd.notna(df_dates["min_d"][0]) else None
max_date = pd.to_datetime(df_dates["max_d"][0]).date() if not df_dates.empty and pd.notna(df_dates["max_d"][0]) else None

if min_date and max_date:
    date_range = st.sidebar.date_input(
        "Période",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        date_debut, date_fin = date_range
    else:
        date_debut, date_fin = min_date, max_date
else:
    st.warning("Aucune donnée en base.")
    st.stop()

enseignes = query("SELECT DISTINCT enseigne FROM tickets ORDER BY enseigne")
liste_enseignes = enseignes["enseigne"].tolist() if not enseignes.empty else []
enseigne_sel = st.sidebar.multiselect("Enseigne", liste_enseignes, default=liste_enseignes)
if not enseigne_sel:
    enseigne_sel = liste_enseignes

st.sidebar.divider()
_state = _load_scan_state()
_last_ts = _state.get("last_scan", 0)
if _last_ts:
    st.sidebar.caption(f"Dernier scan : {datetime.fromtimestamp(_last_ts).strftime('%d/%m %H:%M')}")

if st.sidebar.button("🔄 Mettre à jour"):
    with st.spinner("Analyse en cours…"):
        _ran, _output = run_analyser(force=True)
    if _ran:
        st.sidebar.success("✅ Mise à jour terminée")
        if _output.strip():
            with st.expander("Journal d'analyse"):
                st.text(_output)
        st.rerun()
    else:
        st.sidebar.info(_output)

# Paramètres partagés pour les requêtes filtrées
filtre_params = (date_debut, date_fin, *enseigne_sel)
filtre_enseigne_in = ", ".join(["%s"] * len(enseigne_sel))
filtre_where = f"WHERE t.date BETWEEN %s AND %s AND t.enseigne IN ({filtre_enseigne_in})"

# ─── Titre ────────────────────────────────────────────────────────────────────

st.title("🧾 Analyseur Tickets Système U")

# ─── KPIs ─────────────────────────────────────────────────────────────────────

df_kpi = query(f"""
    SELECT COUNT(*) as nb_tickets,
           SUM(total) as total_depense,
           AVG(total) as moyenne_ticket,
           COUNT(DISTINCT magasin) as nb_magasins,
           COUNT(DISTINCT enseigne) as nb_enseignes
    FROM tickets t
    {filtre_where}
""", filtre_params)

if df_kpi.empty or df_kpi["nb_tickets"][0] == 0:
    st.info("Aucun ticket sur la période sélectionnée.")
    st.stop()

k = df_kpi.iloc[0]
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Tickets", int(k["nb_tickets"]))
col2.metric("Total dépensé", f"{float(k['total_depense']):.2f} €")
col3.metric("Moyenne / ticket", f"{float(k['moyenne_ticket']):.2f} €")
col4.metric("Magasins", int(k["nb_magasins"]))
col5.metric("Enseignes", int(k["nb_enseignes"]))

# Une ligne par semaine sur la période filtrée — len() donne le nb de semaines
df_semaines = query(f"""
    SELECT {db.fmt_week('t.date')} as semaine,
           SUM(t.total) as total_semaine
    FROM tickets t
    {filtre_where}
    GROUP BY semaine
    ORDER BY semaine ASC
""", filtre_params)

nb_semaines = len(df_semaines)
col1, col2, col3, col4, col5 = st.columns(5)
if nb_semaines > 0:
    col1.metric("Nb Semaines", nb_semaines)
    col2.metric("Moyenne / semaine", f"{float(k['total_depense']) / nb_semaines:.2f} €")

st.divider()

# ─── Évolution mensuelle ──────────────────────────────────────────────────────

st.subheader("📈 Évolution mensuelle")

df_mois = query(f"""
    SELECT {db.fmt_month('t.date')} as mois,
           COUNT(*) as nb_tickets,
           SUM(t.total) as total_mois
    FROM tickets t
    {filtre_where}
    GROUP BY {db.fmt_month('t.date')}
    ORDER BY mois
""", filtre_params)

if not df_mois.empty:
    col_left, col_right = st.columns(2)
    with col_left:
        moy_mensuelle = df_mois["total_mois"].mean()
        fig = px.bar(df_mois, x="mois", y="total_mois",
                     labels={"mois": "Mois", "total_mois": "Montant (€)"},
                     title="Montant total par mois")
        fig.add_hline(
            y=moy_mensuelle,
            line_dash="dash", line_color="orange",
            annotation_text=f"Moy. {moy_mensuelle:.0f} €",
            annotation_position="top left",
        )
        st.plotly_chart(fig, width='stretch')
    with col_right:
        fig2 = px.line(df_mois, x="mois", y="nb_tickets", markers=True,
                       labels={"mois": "Mois", "nb_tickets": "Nombre de tickets"},
                       title="Nombre de tickets par mois")
        st.plotly_chart(fig2, width='stretch')

st.divider()

# ─── Top articles ─────────────────────────────────────────────────────────────

st.subheader("🏆 Top articles")

col_n, _ = st.columns([1, 3])
top_n = col_n.slider("Nombre d'articles", min_value=5, max_value=50, value=15)

df_top = query(f"""
    SELECT a.nom,
           COUNT(*) as nb_achats,
           ROUND(SUM(a.quantite), 3) as qte_totale,
           ROUND(AVG(a.prix_unitaire), 2) as prix_moyen,
           ROUND(SUM(a.prix_total), 2) as total_depense
    FROM articles a
    JOIN tickets t ON a.ticket_id = t.id
    {filtre_where}
    GROUP BY a.nom
    ORDER BY total_depense DESC
    LIMIT %s
""", (*filtre_params, top_n))

if not df_top.empty:
    col_left, col_right = st.columns([2, 3])
    with col_left:
        st.dataframe(
            df_top,
            column_config={
                "nom":           st.column_config.TextColumn("Article"),
                "nb_achats":     st.column_config.NumberColumn("Achats", format="%d"),
                "qte_totale":    st.column_config.NumberColumn("Qté", format="%.3g"),
                "prix_moyen":    st.column_config.NumberColumn("Prix moy.", format="%.2f €"),
                "total_depense": st.column_config.NumberColumn("Total", format="%.2f €"),
            },
            width='stretch',
            hide_index=True,
        )

    with col_right:
        fig3 = px.bar(
            df_top.head(15), x="total_depense", y="nom",
            orientation="h",
            labels={"total_depense": "Total (€)", "nom": ""},
            title=f"Top {min(top_n, 15)} articles par montant",
        )
        fig3.update_layout(yaxis={"autorange": "reversed"})
        st.plotly_chart(fig3, width='stretch')

st.divider()

# ─── Répartition par rayon ────────────────────────────────────────────────────

st.subheader("🗂️ Répartition par rayon")

df_rayons = query(f"""
    SELECT COALESCE(a.rayon, 'Non classé') as rayon,
           ROUND(SUM(a.prix_total), 2) as total
    FROM articles a
    JOIN tickets t ON a.ticket_id = t.id
    {filtre_where}
    GROUP BY rayon
    ORDER BY total DESC
""", filtre_params)

if not df_rayons.empty:
    fig4 = px.pie(df_rayons, names="rayon", values="total",
                  title="Dépenses par rayon")
    st.plotly_chart(fig4, width='stretch')

st.divider()

# ─── Liste des tickets ────────────────────────────────────────────────────────

st.subheader("🧾 Tickets")

df_tickets = query(f"""
    SELECT t.date, t.heure, t.magasin, t.numero_ticket,
           t.total, t.mode_paiement, t.parser_name,
           COUNT(a.id) as nb_articles
    FROM tickets t
    LEFT JOIN articles a ON t.id = a.ticket_id
    {filtre_where}
    GROUP BY t.id
    ORDER BY t.date DESC, t.heure DESC
""", filtre_params)

if not df_tickets.empty:
    st.dataframe(
        df_tickets.rename(columns={
            "date": "Date", "heure": "Heure", "magasin": "Magasin",
            "numero_ticket": "N° ticket", "total": "Total (€)",
            "mode_paiement": "Paiement", "parser_name": "Format",
            "nb_articles": "Articles"
        }),
        width='stretch',
        hide_index=True,
    )

st.divider()

# ─── Qualité des données ──────────────────────────────────────────────────────

with st.expander("🔍 Qualité des données"):
    df_ecarts = query(f"""
        SELECT t.numero_ticket, t.fichier,
               t.total as total_ticket,
               ROUND(SUM(a.prix_total), 2) as total_articles,
               ROUND(ABS(t.total - SUM(a.prix_total)), 2) as ecart
        FROM tickets t
        JOIN articles a ON t.id = a.ticket_id
        {filtre_where}
        GROUP BY t.id
        HAVING ecart > 0.01
        ORDER BY ecart DESC
    """, filtre_params)

    if df_ecarts.empty:
        st.success("✅ Tous les totaux sont cohérents")
    else:
        st.warning(f"⚠️ {len(df_ecarts)} ticket(s) avec écart de total")
        st.dataframe(df_ecarts, width='stretch', hide_index=True)

    df_suspects = query("""
        SELECT COUNT(*) as nb FROM articles
        WHERE nom REGEXP '^[0-9]+ x [0-9]+[,.][0-9]+.*$'
    """)
    nb = int(df_suspects["nb"][0]) if not df_suspects.empty else 0
    if nb == 0:
        st.success("✅ Aucun article suspect détecté")
    else:
        st.warning(f"⚠️ {nb} article(s) suspect(s) (lignes de multiplication mal parsées)")
