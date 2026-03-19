#!/usr/bin/env python3
"""
Dashboard web — Analyseur Tickets Système U
Visualisation des données extraites en base MariaDB
"""

import configparser
import os

import pandas as pd
import plotly.express as px
import pymysql
import streamlit as st

# ─── Configuration page ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="Tickets Système U",
    page_icon="🧾",
    layout="wide",
)

# ─── Connexion BD ─────────────────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    config_file = os.environ.get("TICKET_CONFIG", "config.ini")
    config = configparser.ConfigParser()
    config.read(config_file)
    return pymysql.connect(
        host=config.get("database", "host", fallback="localhost"),
        port=config.getint("database", "port", fallback=3306),
        user=config.get("database", "user", fallback="root"),
        password=config.get("database", "password", fallback=""),
        database=config.get("database", "database", fallback="tickets_u"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def query(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    conn.ping(reconnect=True)
    with conn.cursor() as cursor:
        cursor.execute(sql, params or ())
        return pd.DataFrame(cursor.fetchall())


# ─── Sidebar : filtres ────────────────────────────────────────────────────────

st.sidebar.title("🔍 Filtres")

df_dates = query("SELECT MIN(date) as min_d, MAX(date) as max_d FROM tickets")
min_date = pd.to_datetime(df_dates["min_d"][0]).date() if not df_dates.empty else None
max_date = pd.to_datetime(df_dates["max_d"][0]).date() if not df_dates.empty else None

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
    SELECT CONCAT(YEAR(t.date), '_', LPAD(WEEK(t.date), 2, '0')) as semaine,
           SUM(t.total) as total_semaine
    FROM tickets t
    {filtre_where}
    GROUP BY semaine
    ORDER BY semaine ASC
""", filtre_params)

nb_semaines = len(df_semaines)
col1, col2 = st.columns(2)
if nb_semaines > 0:
    col1.metric("Semaines", nb_semaines)
    col2.metric("Moyenne / semaine", f"{float(k['total_depense']) / nb_semaines:.2f} €")

st.divider()

# ─── Évolution mensuelle ──────────────────────────────────────────────────────

st.subheader("📈 Évolution mensuelle")

df_mois = query(f"""
    SELECT DATE_FORMAT(t.date, '%%Y-%%m') as mois,
           COUNT(*) as nb_tickets,
           SUM(t.total) as total_mois
    FROM tickets t
    {filtre_where}
    GROUP BY DATE_FORMAT(t.date, '%%Y-%%m')
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
            df_top.rename(columns={
                "nom": "Article", "nb_achats": "Achats",
                "qte_totale": "Qté", "prix_moyen": "Prix moy. (€)",
                "total_depense": "Total (€)"
            }),
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
