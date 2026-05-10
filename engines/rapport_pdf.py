"""
Générateur de rapport d'audit fiscal PDF (Maroc) v2
Ajout : page "Lignes sources" avec les écritures FEC à l'origine de chaque anomalie
"""
import os
from datetime import datetime
from typing import List
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from engines.bloc1_tva import Anomalie

NAVY   = colors.HexColor("#1a365d")
WHITE  = colors.white
ROUGE  = colors.HexColor("#c53030")
ORANGE = colors.HexColor("#c05621")
VERT   = colors.HexColor("#276749")
GRIS   = colors.HexColor("#e2e8f0")
GRIS_PALE = colors.HexColor("#f7fafc")
VIOLET = colors.HexColor("#553c9a")


def _styles():
    return {
        "titre": ParagraphStyle("titre", fontName="Helvetica-Bold", fontSize=22,
            textColor=NAVY, spaceAfter=6, leading=26),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13,
            textColor=NAVY, spaceBefore=10, spaceAfter=5),
        "corps": ParagraphStyle("corps", fontName="Helvetica", fontSize=9,
            textColor=colors.HexColor("#2d3748"), spaceAfter=4, leading=14),
        "cgi": ParagraphStyle("cgi", fontName="Helvetica-Oblique", fontSize=8,
            textColor=VIOLET, spaceAfter=3),
        "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=7,
            textColor=colors.HexColor("#718096"), leading=10),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=8,
            textColor=VIOLET),
        "sources_hdr": ParagraphStyle("sources_hdr", fontName="Helvetica-Bold",
            fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4),
    }


def generer_rapport_pdf(
    anomalies: List[Anomalie],
    nom_entreprise: str,
    exercice: int,
    blocs_analyses: List[str],
    chemin_sortie: str
) -> str:
    doc = SimpleDocTemplate(
        chemin_sortie, pagesize=A4,
        rightMargin=1.8*cm, leftMargin=1.8*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    S = _styles()
    story = []

    # ── PAGE DE GARDE ──
    story += _page_garde(S, nom_entreprise, exercice, blocs_analyses, anomalies)
    story.append(PageBreak())

    # ── RÉSUMÉ EXÉCUTIF ──
    story += _resume_executif(S, anomalies, nom_entreprise, exercice)

    # ── DÉTAIL PAR BLOC ──
    blocs_presents = list(dict.fromkeys(a.bloc for a in anomalies))
    for bloc in blocs_presents:
        anomalies_bloc = [a for a in anomalies if a.bloc == bloc]
        story += _section_bloc(S, bloc, anomalies_bloc)

    # ── TABLEAU RÉCAPITULATIF ──
    story.append(PageBreak())
    story += _tableau_recap(S, anomalies)

    # ── PAGE LIGNES SOURCES (NOUVEAU) ──
    anomalies_avec_sources = [a for a in anomalies if a.lignes_sources is not None
                               and not a.lignes_sources.empty]
    if anomalies_avec_sources:
        story.append(PageBreak())
        story += _page_lignes_sources(S, anomalies_avec_sources)

    # ── PIED LÉGAL ──
    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRIS))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Ce rapport est généré automatiquement par FiscalAudit Pro. Il constitue une aide à la "
        "décision et ne remplace pas l'avis d'un expert-comptable ou conseiller fiscal. "
        "Références : CGI Maroc (LF 2024/2025/2026), CGNC, Note Circulaire DGI 717.",
        S["footer"]
    ))

    doc.build(story)
    return chemin_sortie


def _page_garde(S, nom, exercice, blocs, anomalies):
    story = []
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("RAPPORT D'AUDIT FISCAL PRÉVENTIF", ParagraphStyle(
        "t", fontName="Helvetica-Bold", fontSize=20, textColor=NAVY,
        spaceBefore=8, spaceAfter=6, alignment=1
    )))
    story.append(Paragraph("Détection automatique d'anomalies et d'incohérences fiscales", ParagraphStyle(
        "st", fontName="Helvetica", fontSize=11, textColor=colors.HexColor("#718096"),
        spaceAfter=10, alignment=1
    )))
    story.append(HRFlowable(width="100%", thickness=2, color=NAVY))
    story.append(Spacer(1, 0.6*cm))

    info = [
        ["Entreprise :", nom],
        ["Exercice analysé :", str(exercice)],
        ["Date du rapport :", datetime.now().strftime("%d/%m/%Y à %H:%M")],
        ["Blocs analysés :", ", ".join(blocs)],
        ["Référentiel :", "CGI Maroc | CGNC | LF 2024/2025/2026"],
    ]
    t = Table(info, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
        ("FONTNAME",(1,0),(1,-1),"Helvetica"),
        ("FONTSIZE",(0,0),(-1,-1),10),
        ("TEXTCOLOR",(0,0),(0,-1),NAVY),
        ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),5),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.8*cm))

    n_c = sum(1 for a in anomalies if a.severity=="CRITIQUE")
    n_m = sum(1 for a in anomalies if a.severity=="MOYEN")
    n_f = sum(1 for a in anomalies if a.severity=="FAIBLE")
    score = _score(anomalies)

    kpi = [[
        Paragraph(str(len(anomalies)), ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=26, textColor=NAVY, alignment=1)),
        Paragraph(str(n_c), ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=26, textColor=ROUGE, alignment=1)),
        Paragraph(str(n_m), ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=26, textColor=ORANGE, alignment=1)),
        Paragraph(f"{score}/100", ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=26, textColor=NAVY, alignment=1)),
    ],[
        Paragraph("Total anomalies", ParagraphStyle("k", fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#718096"), alignment=1)),
        Paragraph("CRITIQUES", ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8, textColor=ROUGE, alignment=1)),
        Paragraph("MOYENNES", ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=8, textColor=ORANGE, alignment=1)),
        Paragraph("Score de risque", ParagraphStyle("k", fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#718096"), alignment=1)),
    ]]
    kt = Table(kpi, colWidths=[4.25*cm]*4)
    kt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),GRIS_PALE),
        ("TOPPADDING",(0,0),(-1,-1),10),
        ("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("BACKGROUND",(1,0),(1,-1),colors.HexColor("#fff5f5")),
    ]))
    story.append(kt)
    return story


def _resume_executif(S, anomalies, nom, exercice):
    story = []
    story.append(Paragraph("RÉSUMÉ EXÉCUTIF", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=1, color=NAVY))
    story.append(Spacer(1, 0.2*cm))
    n_c = sum(1 for a in anomalies if a.severity=="CRITIQUE")
    score = _score(anomalies)
    niveau = "ÉLEVÉ" if score >= 70 else ("MODÉRÉ" if score >= 40 else "FAIBLE")
    story.append(Paragraph(
        f"L'audit de <b>{nom}</b> pour l'exercice <b>{exercice}</b> a identifié "
        f"<b>{len(anomalies)} anomalie(s)</b> dont <b>{n_c} critique(s)</b>. "
        f"Le niveau de risque fiscal est estimé <b>{niveau}</b> (score {score}/100).",
        S["corps"]
    ))
    if n_c > 0:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(
            "⚠ Des anomalies CRITIQUES ont été détectées. Ces points sont susceptibles d'être "
            "relevés lors d'un contrôle DGI.",
            ParagraphStyle("w", fontName="Helvetica-Bold", fontSize=9, textColor=ROUGE,
                spaceAfter=8, backColor=colors.HexColor("#fff5f5"),
                borderColor=ROUGE, borderWidth=1, borderPadding=6)
        ))
    story.append(Spacer(1, 0.3*cm))
    return story


def _section_bloc(S, bloc, anomalies):
    story = []
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(bloc.upper(), S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRIS))
    story.append(Spacer(1, 0.2*cm))

    sev_color = {"CRITIQUE": ROUGE, "MOYEN": ORANGE, "FAIBLE": VERT}
    sev_bg    = {"CRITIQUE": colors.HexColor("#fff5f5"),
                 "MOYEN":    colors.HexColor("#fffaf0"),
                 "FAIBLE":   colors.HexColor("#f0fff4")}

    for a in anomalies:
        sc = sev_color.get(a.severity, colors.grey)
        bg = sev_bg.get(a.severity, GRIS_PALE)

        rows = [
            [Paragraph(f"<b>{a.code}</b>", S["code"]),
             Paragraph(f"<b>{a.severity}</b>",
                       ParagraphStyle("sev", fontName="Helvetica-Bold", fontSize=9, textColor=sc, alignment=2))],
            [Paragraph(f"<b>Anomalie :</b> {a.description}", S["corps"]), ""],
            [Paragraph(f"<b>Réf. CGI :</b> {a.reference_cgi}", S["cgi"]), ""],
            [Paragraph(f"<b>Action :</b> {a.recommandation}", S["corps"]), ""],
        ]
        if a.montant:
            rows.append([Paragraph(f"<b>Montant estimé :</b> {a.montant:,.0f} MAD",
                ParagraphStyle("m", fontName="Helvetica-Bold", fontSize=9, textColor=sc)), ""])
        if a.detail:
            rows.append([Paragraph(f"<i>{a.detail}</i>",
                ParagraphStyle("d", fontName="Helvetica-Oblique", fontSize=8,
                    textColor=colors.HexColor("#718096"))), ""])

        t = Table(rows, colWidths=[14.5*cm, 2.5*cm])
        style_cmds = [
            ("BACKGROUND",(0,0),(-1,-1),bg),
            ("LINEABOVE",(0,0),(-1,0),2,sc),
            ("LEFTPADDING",(0,0),(-1,-1),8),
            ("RIGHTPADDING",(0,0),(-1,-1),8),
            ("TOPPADDING",(0,0),(-1,-1),4),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
        ]
        for i in range(1, len(rows)):
            style_cmds.append(("SPAN",(0,i),(-1,i)))
        t.setStyle(TableStyle(style_cmds))
        story.append(KeepTogether(t))
        story.append(Spacer(1, 0.15*cm))

    return story


def _tableau_recap(S, anomalies):
    story = []
    story.append(Paragraph("TABLEAU RÉCAPITULATIF DES ANOMALIES", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=1, color=NAVY))
    story.append(Spacer(1, 0.2*cm))

    headers = ["Code", "Bloc", "Sévérité", "Compte", "Montant (MAD)", "Description"]
    data = [headers]
    for a in sorted(anomalies, key=lambda x: {"CRITIQUE":0,"MOYEN":1,"FAIBLE":2}.get(x.severity,3)):
        m = f"{a.montant:,.0f}" if a.montant else "—"
        desc = a.description[:65] + "..." if len(a.description) > 65 else a.description
        data.append([a.code, a.bloc.split(" - ")[0], a.severity, a.compte or "—", m, desc])

    t = Table(data, colWidths=[2.5*cm, 2.5*cm, 1.8*cm, 2.5*cm, 2.5*cm, 5.7*cm], repeatRows=1)
    cmds = [
        ("BACKGROUND",(0,0),(-1,0),NAVY),
        ("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),7.5),
        ("FONTNAME",(0,1),(-1,-1),"Helvetica"),
        ("GRID",(0,0),(-1,-1),0.4,GRIS),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,GRIS_PALE]),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]
    for i, row in enumerate(data[1:], 1):
        sev = row[2]
        c = ROUGE if sev == "CRITIQUE" else ORANGE if sev == "MOYEN" else VERT
        cmds.append(("TEXTCOLOR",(2,i),(2,i),c))
        cmds.append(("FONTNAME",(2,i),(2,i),"Helvetica-Bold"))
    t.setStyle(TableStyle(cmds))
    story.append(t)
    return story


def _page_lignes_sources(S, anomalies):
    """NOUVELLE page : lignes FEC/GL à l'origine de chaque anomalie."""
    story = []
    story.append(Paragraph("LIGNES SOURCES — ÉCRITURES À L'ORIGINE DES ANOMALIES", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY))
    story.append(Paragraph(
        "Cette page présente les écritures comptables extraites du fichier FEC/Grand Livre "
        "qui ont déclenché chaque anomalie. Ces lignes permettent une vérification rapide "
        "et une correction précise.",
        S["corps"]
    ))
    story.append(Spacer(1, 0.4*cm))

    sev_color = {"CRITIQUE": ROUGE, "MOYEN": ORANGE, "FAIBLE": VERT}

    for a in anomalies:
        if a.lignes_sources is None or a.lignes_sources.empty:
            continue

        sc = sev_color.get(a.severity, colors.grey)

        # En-tête anomalie
        story.append(Paragraph(
            f"<b>[{a.severity}] {a.code}</b> — {a.description[:80]}",
            ParagraphStyle("ah", fontName="Helvetica-Bold", fontSize=9,
                textColor=sc, spaceBefore=8, spaceAfter=4)
        ))

        # Tableau des lignes sources
        df_src = a.lignes_sources.copy()

        # Formater colonnes
        for col in df_src.columns:
            if "date" in col.lower():
                try:
                    df_src[col] = pd.to_datetime(df_src[col]).dt.strftime("%d/%m/%Y")
                except Exception:
                    pass
            elif df_src[col].dtype in [float, int] or col.lower() in ["debit","credit"]:
                try:
                    df_src[col] = df_src[col].apply(
                        lambda x: f"{float(x):,.0f}" if x not in [None,"","nan","0","0.0"] else ""
                    )
                except Exception:
                    pass
            df_src[col] = df_src[col].astype(str).fillna("").str[:40]

        # Largeurs adaptées selon colonnes
        headers_src = list(df_src.columns)
        n_cols = len(headers_src)
        col_width = 17.0 / max(n_cols, 1)
        col_widths = [col_width * cm] * n_cols

        data_src = [headers_src] + df_src.values.tolist()
        t_src = Table(data_src, colWidths=col_widths, repeatRows=1)
        t_src.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8eaf6")),
            ("TEXTCOLOR",(0,0),(-1,0),NAVY),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),7),
            ("FONTNAME",(0,1),(-1,-1),"Helvetica"),
            ("GRID",(0,0),(-1,-1),0.3,GRIS),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,GRIS_PALE]),
            ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(KeepTogether([t_src, Spacer(1, 0.2*cm)]))

    return story


import pandas as pd  # needed for _page_lignes_sources


def _score(anomalies):
    return min(sum(15 if a.severity=="CRITIQUE" else 7 if a.severity=="MOYEN" else 2
                   for a in anomalies), 100)
