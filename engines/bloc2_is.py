""" 
BLOC 2 — Audit IS / Résultat Fiscal (Maroc)
Sources: CGI Art. 8-20, Note circulaire 717 DGI, Upsilon Consulting
"""
import pandas as pd
from typing import List, Optional
from .bloc1_tva import Anomalie, _normaliser_grand_livre, _solde_compte, _get_col
from .normalisation import normaliser_df_gl

# Taux IS 2025-2026 (barème proportionnel LF2024)
BAREME_IS = [
    (300_000, 0.10),
    (1_000_000, 0.20),
    (5_000_000, 0.26),
    (float("inf"), 0.31),
]

TAUX_CM = 0.0025  # Cotisation Minimale 0.25% du CA HT (Art. 144 CGI)

# Charges JAMAIS déductibles fiscalement (à réintégrer)
COMPTES_NON_DEDUCTIBLES = {
    "6582": ("Amendes et pénalités", "Art. 11-II CGI — Amendes jamais déductibles"),
    "6598": ("Autres charges non courantes", "Art. 11-II CGI"),
}

# Amortissements — taux maximum linéaire (circulaire 717)
TAUX_AMORT_MAX = {
    "2321": ("Bâtiments commerciaux", 0.05),
    "2322": ("Bâtiments industriels", 0.05),
    "233":  ("Agencements/installations", 0.10),
    "2340": ("Matériel et outillage", 0.15),
    "2350": ("Matériel informatique", 0.20),
    "2410": ("Mobilier de bureau", 0.10),
    "2420": ("Matériel de bureau", 0.15),
    "2430": ("Matériel de transport", 0.20),
    "2431": ("Véhicules tourisme", 0.20),
}

# Plafond amortissement véhicules tourisme (Art. 10-II CGI)
PLAFOND_VEH_TOURISME = 300_000  # MAD HT (Art. 10 CGI — 300K ou 400K selon LF)


def analyser_is(
    grand_livre: pd.DataFrame,
    balance: pd.DataFrame,
    ca_exercice: float = None,
    resultat_comptable: float = None,
    exercice: int = None
) -> List[Anomalie]:
    """Analyse IS, charges déductibles, amortissements, cotisation minimale."""
    anomalies = []
    gl = normaliser_df_gl(grand_livre)

    # ── TEST 1 : Amendes et pénalités non réintégrées ──
    for cpte, (label, ref) in COMPTES_NON_DEDUCTIBLES.items():
        mask = gl["compte"].str.startswith(cpte)
        montant = gl[mask]["debit"].sum()
        if montant > 0:
            anomalies.append(Anomalie(
                bloc="BLOC 2 - IS",
                code=f"IS-001-{cpte}",
                severity="CRITIQUE",
                compte=cpte,
                montant=montant,
                description=f"Compte {cpte} ({label}) : {montant:,.2f} MAD — charge fiscalement non déductible",
                reference_cgi=ref,
                recommandation=f"Réintégrer {montant:,.2f} MAD dans le passage RNC→RNF (tableau de détermination du résultat fiscal).",
                detail=None
            ))

    # ── TEST 2 : Intérêts comptes courants associés (taux plafond) ──
    mask_cca = gl["compte"].str.startswith("6311")  # Intérêts sur CCA
    interet_cca = gl[mask_cca]["debit"].sum()
    if interet_cca > 0:
        # Taux plafond DGI = taux BAM + 2 points environ (varie chaque année)
        # Ici on signale sans calculer précisément (nécessite solde CCA)
        anomalies.append(Anomalie(
            bloc="BLOC 2 - IS",
            code="IS-002",
            severity="MOYEN",
            compte="6311",
            montant=interet_cca,
            description=f"Intérêts sur comptes courants associés : {interet_cca:,.2f} MAD — vérifier taux vs plafond DGI",
            reference_cgi="Art. 10-I-B-3° CGI — Intérêts déductibles plafonnés au taux BAM + marge",
            recommandation="Calculer le taux effectif appliqué et comparer au taux maximum DGI en vigueur. Réintégrer l'excédent.",
            detail="Taux plafond publié annuellement par la DGI (note circulaire)"
        ))

    # ── TEST 3 : Amortissements véhicules tourisme ──
    # FIX : utiliser uniquement les acquisitions de l'exercice (journal != AN)
    # pour éviter de doubler avec le RAN (solde d'ouverture)
    mask_immo_veh = gl["compte"].str.startswith("2431")
    
    # Acquisitions exercice = écritures hors journal AN
    if "journal" in gl.columns:
        mask_acq = mask_immo_veh & (~gl["journal"].str.upper().str.strip().isin(["AN","A-NOUVEAUX"]))
    else:
        mask_acq = mask_immo_veh
    
    valeur_acq = gl[mask_acq]["debit"].sum()  # Acquisitions de l'exercice
    
    # Valeur brute totale = max(acquisitions, solde ouverture) pour ne pas doubler
    # Si pas d'acquisition cette année, prendre le solde RAN
    mask_ran = mask_immo_veh & gl.get("journal", pd.Series([""] * len(gl))).str.upper().str.strip().isin(["AN","A-NOUVEAUX"])
    valeur_ran = gl[mask_ran]["debit"].sum()
    
    # La valeur de référence = la plus grande des deux (acquisition ou RAN si pas d'achat cette année)
    if valeur_acq > 0:
        valeur_veh = valeur_acq  # Véhicule acheté cette année
    else:
        valeur_veh = valeur_ran  # Véhicule existant depuis avant
    
    if valeur_veh > PLAFOND_VEH_TOURISME:
        amort_max = PLAFOND_VEH_TOURISME * 0.20
        amort_reel = valeur_veh * 0.20
        excedent = max(0, amort_reel - amort_max)
        if excedent > 0:
            anomalies.append(Anomalie(
                bloc="BLOC 2 - IS",
                code="IS-003",
                severity="CRITIQUE",
                compte="2431 / 6193",
                montant=excedent,
                description=f"Véhicule tourisme valeur {valeur_veh:,.2f} MAD > plafond {PLAFOND_VEH_TOURISME:,.0f} MAD — amort. excédentaire {excedent:,.2f} MAD",
                reference_cgi="Art. 10-II CGI — Plafond amortissement véhicules tourisme",
                recommandation=f"Réintégrer {excedent:,.2f} MAD dans le résultat fiscal. Base amortissable plafonnée à {PLAFOND_VEH_TOURISME:,.0f} MAD.",
                detail=f"Amortissement déductible max : {amort_max:,.2f} MAD/an"
            ))

    # ── TEST 4 : Taux d'amortissement hors normes ──
    anomalies_amort = _verifier_taux_amortissement(gl)
    anomalies.extend(anomalies_amort)

    # ── TEST 5 : Dons dépassant 2‰ du CA ──
    mask_dons = gl["compte"].str.startswith("6187")
    montant_dons = gl[mask_dons]["debit"].sum()
    if montant_dons > 0 and ca_exercice and ca_exercice > 0:
        plafond_dons = ca_exercice * 0.002
        if montant_dons > plafond_dons:
            excedent_don = montant_dons - plafond_dons
            anomalies.append(Anomalie(
                bloc="BLOC 2 - IS",
                code="IS-005",
                severity="MOYEN",
                compte="6187",
                montant=excedent_don,
                description=f"Dons {montant_dons:,.2f} MAD > plafond 2‰ CA ({plafond_dons:,.2f} MAD) — excédent {excedent_don:,.2f} MAD",
                reference_cgi="Art. 10-I-B-2° CGI — Dons déductibles limités à 2‰ du CA HT",
                recommandation=f"Réintégrer {excedent_don:,.2f} MAD dans le résultat fiscal.",
                detail=f"CA exercice : {ca_exercice:,.2f} MAD | Plafond : {plafond_dons:,.2f} MAD"
            ))

    # ── TEST 6 : Cotisation Minimale (CM) ──
    if ca_exercice and ca_exercice > 0 and resultat_comptable is not None:
        anomalies_cm = _verifier_cotisation_minimale(gl, ca_exercice, resultat_comptable)
        anomalies.extend(anomalies_cm)

    # ── TEST 7 : Provisions non déductibles ──
    anomalies_prov = _verifier_provisions(gl)
    anomalies.extend(anomalies_prov)

    # ── TEST 8 : Charges sans pièces probantes (gros montants en espèces) ──
    anomalies_cash = _verifier_paiements_especes(gl)
    anomalies.extend(anomalies_cash)

    return anomalies


def _verifier_taux_amortissement(gl: pd.DataFrame) -> List[Anomalie]:
    """Détecte taux d'amort. hors normes sur les comptes 619x."""
    anomalies = []
    # Comptes de dotations amortissement
    mask_da = gl["compte"].str.match(r"^619")
    total_da = gl[mask_da]["debit"].sum()
    
    # Comptes immobilisations
    mask_immo = gl["compte"].str.match(r"^2[23]")
    total_immo = gl[mask_immo]["debit"].sum()
    
    if total_immo > 0 and total_da > 0:
        taux_effectif = total_da / total_immo
        if taux_effectif > 0.33:  # Plus de 33% en moyenne = suspect
            anomalies.append(Anomalie(
                bloc="BLOC 2 - IS",
                code="IS-004",
                severity="MOYEN",
                compte="619x",
                montant=total_da,
                description=f"Taux d'amortissement moyen élevé : {taux_effectif:.1%} (DA={total_da:,.0f}/Immos={total_immo:,.0f})",
                reference_cgi="Art. 10-II CGI + Circulaire 717 — Taux maximum par catégorie d'immobilisation",
                recommandation="Vérifier les taux par catégorie : bâtiments 4-5%, matériel 10-20%, informatique 20-25%.",
                detail=f"Taux moyen calculé : {taux_effectif:.1%}"
            ))
    return anomalies


def _verifier_cotisation_minimale(
    gl: pd.DataFrame, ca: float, resultat_comptable: float
) -> List[Anomalie]:
    """Vérifie que la CM a été calculée correctement."""
    anomalies = []
    cm_theorique = ca * TAUX_CM
    
    # Chercher IS payé dans les charges (compte 6701)
    mask_is = gl["compte"].str.startswith("6701")
    is_comptabilise = gl[mask_is]["debit"].sum()
    
    if is_comptabilise < cm_theorique and is_comptabilise > 0:
        diff = cm_theorique - is_comptabilise
        anomalies.append(Anomalie(
            bloc="BLOC 2 - IS",
            code="IS-006",
            severity="CRITIQUE",
            compte="6701 / 4452",
            montant=diff,
            description=f"IS comptabilisé ({is_comptabilise:,.2f} MAD) < Cotisation Minimale théorique ({cm_theorique:,.2f} MAD)",
            reference_cgi="Art. 144 CGI — CM = 0,25% × (CA + produits accessoires + produits financiers)",
            recommandation=f"Vérifier si une exonération CM est applicable. Sinon, régulariser : IS dû ≥ CM = {cm_theorique:,.2f} MAD.",
            detail=f"CA = {ca:,.2f} MAD | CM = {cm_theorique:,.2f} MAD | IS déclaré = {is_comptabilise:,.2f} MAD"
        ))
    elif is_comptabilise == 0 and ca > 300_000:
        anomalies.append(Anomalie(
            bloc="BLOC 2 - IS",
            code="IS-006b",
            severity="CRITIQUE",
            compte="4452",
            montant=cm_theorique,
            description=f"Aucun IS comptabilisé (compte 6701/4452 absent) — CM théorique : {cm_theorique:,.2f} MAD",
            reference_cgi="Art. 144 CGI — Cotisation Minimale obligatoire même en cas de déficit",
            recommandation="Calculer et provisionner la cotisation minimale même si l'exercice est déficitaire.",
            detail=None
        ))
    return anomalies


def _verifier_provisions(gl: pd.DataFrame) -> List[Anomalie]:
    """Provisions générales = non déductibles fiscalement."""
    anomalies = []
    # Provisions pour risques généraux (non individualisées)
    mask_prov_gen = gl["compte"].str.match(r"^6195")  # Dotations provisions risques généraux
    montant_prov_gen = gl[mask_prov_gen]["debit"].sum()
    
    if montant_prov_gen > 0:
        anomalies.append(Anomalie(
            bloc="BLOC 2 - IS",
            code="IS-007",
            severity="MOYEN",
            compte="6195",
            montant=montant_prov_gen,
            description=f"Provisions générales : {montant_prov_gen:,.2f} MAD — fiscalement non déductibles",
            reference_cgi="Art. 10-I-F CGI — Seules les provisions individualisées et probables sont déductibles",
            recommandation="Réintégrer les provisions générales au résultat fiscal. Seules les provisions pour créances douteuses individualisées sont admises.",
            detail=None
        ))
    return anomalies


def _verifier_paiements_especes(gl: pd.DataFrame) -> List[Anomalie]:
    """Paiements en espèces > 10 000 MAD = déductibilité limitée."""
    anomalies = []
    if "libelle" not in gl.columns:
        return anomalies
    # Chercher mentions "espèce", "cash" dans libellés avec gros montants
    mask_cash = gl["libelle"].astype(str).str.lower().str.contains("espèce|espece|cash|liquide", na=False)
    mask_charge = gl["compte"].str.match(r"^6")
    mask = mask_cash & mask_charge
    
    lignes_cash = gl[mask]
    lignes_suspects = lignes_cash[lignes_cash["debit"] > 10_000]
    
    if len(lignes_suspects) > 0:
        montant_total = lignes_suspects["debit"].sum()
        anomalies.append(Anomalie(
            bloc="BLOC 2 - IS",
            code="IS-008",
            severity="MOYEN",
            compte="6xxx",
            montant=montant_total,
            description=f"{len(lignes_suspects)} paiement(s) en espèces > 10 000 MAD détecté(s) : {montant_total:,.2f} MAD",
            reference_cgi="Art. 11-III CGI — Charges payées en espèces > 10 000 MAD : déductibilité réduite à 50%",
            recommandation="Vérifier les pièces justificatives. Les paiements > 10 000 MAD en espèces sont partiellement non déductibles.",
            detail=f"{len(lignes_suspects)} lignes concernées"
        ))
    return anomalies
