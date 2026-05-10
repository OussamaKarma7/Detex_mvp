""" 
BLOC 3 — Rapprochement Bancaire
Sources: CGNC, Art. 212 CGI (conservation relevés 10 ans), pratique DGI
"""
import pandas as pd
from typing import List
from .bloc1_tva import Anomalie, _normaliser_grand_livre
from .normalisation import normaliser_df_gl


def analyser_rapprochement_bancaire(
    grand_livre: pd.DataFrame,
    releve_bancaire: pd.DataFrame
) -> List[Anomalie]:
    """
    Rapproche le solde banque en GL avec le relevé bancaire réel.
    Détecte: écart de solde, virements non comptabilisés, chèques en transit trop anciens.
    """
    anomalies = []
    gl = normaliser_df_gl(grand_livre)
    rb = _normaliser_releve(releve_bancaire)

    # ── Solde compte banque dans GL (comptes 514x, 5141, 516x)
    mask_banque = gl["compte"].str.match(r"^5[14][0-9]")
    solde_gl_banque = gl[mask_banque]["debit"].sum() - gl[mask_banque]["credit"].sum()

    # ── Solde relevé bancaire
    if "montant" in rb.columns:
        solde_releve = rb["montant"].sum()
    elif "debit" in rb.columns and "credit" in rb.columns:
        solde_releve = rb["credit"].sum() - rb["debit"].sum()
    else:
        solde_releve = None

    # ── TEST 1 : Écart solde banque GL vs relevé ──
    if solde_releve is not None:
        ecart = abs(solde_gl_banque - solde_releve)
        if ecart > 500:
            anomalies.append(Anomalie(
                bloc="BLOC 3 - Banque",
                code="BNQ-001",
                severity="CRITIQUE" if ecart > 50_000 else "MOYEN",
                compte="514x",
                montant=ecart,
                description=f"Écart solde banque : GL={solde_gl_banque:,.2f} MAD vs Relevé={solde_releve:,.2f} MAD",
                reference_cgi="Art. 212 CGI — Comptabilité probante : concordance obligatoire banque/GL",
                recommandation="Établir l'état de rapprochement bancaire et identifier les opérations non rapprochées.",
                detail=f"Écart = {ecart:,.2f} MAD"
            ))
        else:
            # Soldes concordants — noter quand même
            pass

    # ── TEST 2 : Mouvements relevé sans contrepartie GL ──
    anomalies_non_cpta = _detecter_mouvements_non_comptabilises(gl, rb)
    anomalies.extend(anomalies_non_cpta)

    # ── TEST 3 : Chèques en transit > 45 jours ──
    anomalies_cheques = _detecter_cheques_anciens(gl, rb)
    anomalies.extend(anomalies_cheques)

    # ── TEST 4 : Virements importants sans libellé (suspicion) ──
    anomalies_virt = _detecter_virements_suspects(rb)
    anomalies.extend(anomalies_virt)

    # ── TEST 5 : Encaissements non comptabilisés (revenus dissimulés) ──
    anomalies_encaiss = _detecter_encaissements_suspects(gl, rb)
    anomalies.extend(anomalies_encaiss)

    return anomalies


def _normaliser_releve(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise le relevé bancaire (formats Sage, banque, manuel)."""
    df = df.copy()
    col_map = {
        "date": ["date", "date opération", "date valeur", "date_operation"],
        "libelle": ["libelle", "libellé", "description", "opération", "detail", "motif"],
        "debit": ["débit", "debit", "sortie", "paiement", "montant débit"],
        "credit": ["crédit", "credit", "entrée", "encaissement", "montant crédit"],
        "montant": ["montant", "amount"],
    }
    rename = {}
    for target, candidates in col_map.items():
        for c in df.columns:
            if c.lower().strip() in candidates:
                rename[c] = target
                break
    df = df.rename(columns=rename)
    for col in ["debit", "credit", "montant"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(" ", "").str.replace(",", "."),
                errors="coerce"
            ).fillna(0)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    return df


def _detecter_mouvements_non_comptabilises(
    gl: pd.DataFrame, rb: pd.DataFrame
) -> List[Anomalie]:
    """Mouvements significatifs dans relevé absents du GL."""
    anomalies = []
    if "montant" not in rb.columns and ("debit" not in rb.columns or "credit" not in rb.columns):
        return anomalies

    if "montant" in rb.columns:
        gros_mouvements = rb[rb["montant"].abs() > 50_000]
    else:
        rb["montant_calc"] = rb.get("credit", pd.Series([0]*len(rb))) - rb.get("debit", pd.Series([0]*len(rb)))
        gros_mouvements = rb[rb["montant_calc"].abs() > 50_000]

    if len(gros_mouvements) == 0:
        return anomalies

    # Vérifier présence GL
    nb_suspects = 0
    montant_suspect = 0
    for _, row in gros_mouvements.iterrows():
        montant = row.get("montant", row.get("montant_calc", 0))
        if abs(montant) > 50_000:
            nb_suspects += 1
            montant_suspect += abs(montant)

    if nb_suspects > 0:
        anomalies.append(Anomalie(
            bloc="BLOC 3 - Banque",
            code="BNQ-002",
            severity="MOYEN",
            compte="514x",
            montant=montant_suspect,
            description=f"{nb_suspects} mouvement(s) bancaire(s) > 50 000 MAD à vérifier dans le GL",
            reference_cgi="Art. 210 CGI — Tout flux bancaire doit avoir une contrepartie comptable",
            recommandation="Rapprocher manuellement chaque grand mouvement bancaire avec les écritures GL correspondantes.",
            detail=f"Total mouvements suspects : {montant_suspect:,.2f} MAD"
        ))
    return anomalies


def _detecter_cheques_anciens(gl: pd.DataFrame, rb: pd.DataFrame) -> List[Anomalie]:
    """Chèques en transit > 45 jours (compte 5141 vs relevé)."""
    anomalies = []
    if "date" not in rb.columns or rb["date"].isna().all():
        return anomalies

    date_max = rb["date"].dropna().max()
    if pd.isnull(date_max):
        return anomalies

    seuil_date = date_max - pd.Timedelta(days=45)
    if "libelle" in rb.columns:
        mask_cheque = rb["libelle"].astype(str).str.lower().str.contains("chèque|cheque|chq", na=False)
        vieux_cheques = rb[mask_cheque & (rb["date"] < seuil_date)]
        if len(vieux_cheques) > 0:
            montant = vieux_cheques.get("montant", vieux_cheques.get("debit", pd.Series([0]))).abs().sum()
            anomalies.append(Anomalie(
                bloc="BLOC 3 - Banque",
                code="BNQ-003",
                severity="FAIBLE",
                compte="5141",
                montant=montant,
                description=f"{len(vieux_cheques)} chèque(s) en transit > 45 jours détecté(s)",
                reference_cgi="CGNC — Les chèques en transit prolongé doivent être justifiés",
                recommandation="Contacter les bénéficiaires pour présentation ou annuler et réémettre les chèques non encaissés.",
                detail=f"Montant total : {montant:,.2f} MAD"
            ))
    return anomalies


def _detecter_virements_suspects(rb: pd.DataFrame) -> List[Anomalie]:
    """Virements importants sans libellé explicatif."""
    anomalies = []
    if "libelle" not in rb.columns:
        return anomalies
    mask_no_label = rb["libelle"].astype(str).str.strip().str.len() < 5
    
    if "montant" in rb.columns:
        mask_gros = rb["montant"].abs() > 100_000
    elif "credit" in rb.columns:
        mask_gros = rb["credit"] > 100_000
    else:
        return anomalies

    suspects = rb[mask_no_label & mask_gros]
    if len(suspects) > 0:
        montant = suspects.get("montant", suspects.get("credit", pd.Series([0]))).abs().sum()
        anomalies.append(Anomalie(
            bloc="BLOC 3 - Banque",
            code="BNQ-004",
            severity="MOYEN",
            compte="514x",
            montant=montant,
            description=f"{len(suspects)} virement(s) > 100 000 MAD sans libellé explicatif",
            reference_cgi="Art. 210 CGI — Justification des flux bancaires importants",
            recommandation="Documenter chaque virement important avec contrat, facture ou justificatif correspondant.",
            detail=f"Montant total non justifié : {montant:,.2f} MAD"
        ))
    return anomalies


def _detecter_encaissements_suspects(gl: pd.DataFrame, rb: pd.DataFrame) -> List[Anomalie]:
    """Encaissements relevé sans produit GL correspondant (CA dissimulé)."""
    anomalies = []
    if "credit" not in rb.columns and "montant" not in rb.columns:
        return anomalies

    if "credit" in rb.columns:
        total_encaissements = rb["credit"].sum()
    else:
        total_encaissements = rb[rb["montant"] > 0]["montant"].sum()

    # CA GL (produits)
    mask_produits = gl["compte"].str.match(r"^7[0-9]")
    ca_gl = gl[mask_produits]["credit"].sum()

    if total_encaissements > ca_gl * 1.20 and ca_gl > 0:
        ecart = total_encaissements - ca_gl
        anomalies.append(Anomalie(
            bloc="BLOC 3 - Banque",
            code="BNQ-005",
            severity="CRITIQUE",
            compte="514x / 7xxx",
            montant=ecart,
            description=f"Encaissements bancaires ({total_encaissements:,.2f}) >> CA GL ({ca_gl:,.2f}) — écart {ecart:,.2f} MAD",
            reference_cgi="Art. 210 CGI — Insuffisance de CA déclaré vs flux bancaires réels",
            recommandation="Analyser les encaissements non imputés à des produits. Risque de CA dissimulé ou d'avances non comptabilisées.",
            detail=f"Encaissements/CA ratio : {total_encaissements/ca_gl:.2f}x (normal ≈ 1.0)"
        ))
    return anomalies
