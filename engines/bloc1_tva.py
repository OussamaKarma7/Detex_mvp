"""
BLOC 1 — Audit TVA (Maroc)
CGI Art. 87-125, LF 2024 Art. 117-V
"""
import pandas as pd
from typing import List, Optional
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class Anomalie:
    bloc: str
    code: str
    severity: str
    compte: Optional[str]
    montant: Optional[float]
    description: str
    reference_cgi: str
    recommandation: str
    detail: Optional[str] = None
    lignes_sources: Optional[pd.DataFrame] = None  # NEW: lignes FEC à l'origine


def _normaliser_grand_livre(df: pd.DataFrame) -> pd.DataFrame:
    from engines.normalisation import normaliser_df_gl
    return normaliser_df_gl(df)


def _calculer_ca_gl(gl: pd.DataFrame) -> float:
    mask = gl["compte"].str.match(r"^7[0-9]")
    return gl[mask]["credit"].sum() - gl[mask]["debit"].sum()


def _solde_compte(gl: pd.DataFrame, compte_prefix: str) -> float:
    mask = gl["compte"].str.startswith(compte_prefix)
    return gl[mask]["debit"].sum() - gl[mask]["credit"].sum()


def _get_col(df: pd.DataFrame, candidates: list) -> pd.Series:
    for c in df.columns:
        if c.lower().strip() in [x.lower() for x in candidates]:
            return pd.to_numeric(df[c], errors="coerce").fillna(0)
    return pd.Series([0.0])


def _extraire_lignes(gl: pd.DataFrame, comptes: list) -> Optional[pd.DataFrame]:
    """Extrait les lignes GL correspondant aux comptes pour le rapport."""
    if gl.empty:
        return None
    mask = pd.Series([False] * len(gl))
    for cpte in comptes:
        mask |= gl["compte"].str.startswith(str(cpte))
    lignes = gl[mask].copy()
    if lignes.empty:
        return None
    cols = [c for c in ["date", "journal", "compte", "libelle", "debit", "credit"] if c in lignes.columns]
    return lignes[cols].head(30).reset_index(drop=True)


def analyser_tva(
    grand_livre: pd.DataFrame,
    declarations_tva: pd.DataFrame,
    exercice: int = None
) -> List[Anomalie]:
    anomalies = []
    gl = _normaliser_grand_livre(grand_livre)
    if exercice is None:
        exercice = datetime.now().year - 1

    ca_gl = _calculer_ca_gl(gl)

    # ── TEST 1 : Écart CA GL vs CA déclaré TVA ──
    if not declarations_tva.empty:
        ca_declare_tva = _get_col(declarations_tva,
            ["base_taxable", "base taxable", "ca_taxable"]).sum()

        if ca_declare_tva > 0 and ca_gl > 0:
            ecart = abs(ca_gl - ca_declare_tva)
            if ecart > max(ca_gl * 0.01, 1000):
                pct = ecart / ca_gl * 100
                anomalies.append(Anomalie(
                    bloc="BLOC 1 - TVA", code="TVA-001",
                    severity="CRITIQUE" if pct > 5 else "MOYEN",
                    compte="7xxx / 4455", montant=ecart,
                    description=f"CA Grand Livre ({ca_gl:,.0f} MAD) ≠ CA déclaré TVA ({ca_declare_tva:,.0f} MAD)",
                    reference_cgi="Art. 99 & 210 CGI — Recoupement DGI systématique IS vs TVA",
                    recommandation="Vérifier avances clients (4491), opérations hors champ, exports non taxés.",
                    detail=f"Écart = {ecart:,.0f} MAD ({pct:.1f}%)",
                    lignes_sources=_extraire_lignes(gl, ["7111","7113","7124","4455"])
                ))

        # ── TEST 2 : TVA collectée GL vs déclarée ──
        tva_gl = gl[gl["compte"].str.startswith("4455")]["credit"].sum()
        tva_dec = _get_col(declarations_tva, ["tva_collectee","tva collectée"]).sum()
        if tva_gl > 0 and tva_dec > 0 and abs(tva_gl - tva_dec) > 500:
            ecart = abs(tva_gl - tva_dec)
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code="TVA-002", severity="CRITIQUE",
                compte="4455", montant=ecart,
                description=f"TVA collectée GL ({tva_gl:,.0f}) ≠ TVA déclarée ({tva_dec:,.0f})",
                reference_cgi="Art. 110 CGI — TVA collectée doit être intégralement reversée",
                recommandation="Pointer chaque déclaration mensuelle contre le solde 4455.",
                detail=f"Écart = {ecart:,.0f} MAD",
                lignes_sources=_extraire_lignes(gl, ["4455"])
            ))

    # ── TEST 3 : Solde 4455 débiteur ──
    if gl["compte"].str.startswith("4455").any():
        solde = _solde_compte(gl, "4455")
        if solde < -500:
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code="TVA-003", severity="CRITIQUE",
                compte="4455", montant=abs(solde),
                description=f"Compte 4455 solde DÉBITEUR ({abs(solde):,.0f} MAD) — TVA non reversée",
                reference_cgi="Art. 110 & 114 CGI — Reversement obligatoire dans le trimestre",
                recommandation="Analyser les écritures débitrices du 4455.",
                lignes_sources=_extraire_lignes(gl, ["4455"])
            ))

    # ── TEST 4 : Anciens taux (>2026) ──
    for taux in ["7%", "14%", "16%"]:
        mask = gl["libelle"].str.contains(taux, na=False)
        nb = mask.sum()
        if nb > 0 and exercice >= 2026:
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code=f"TVA-004-{taux}",
                severity="CRITIQUE", compte="4455",
                montant=gl[mask]["debit"].sum() + gl[mask]["credit"].sum(),
                description=f"{nb} écriture(s) avec taux {taux} — supprimé en 2026",
                reference_cgi="LF 2024 — Convergence 2 taux (20%/10%) à partir de 2026",
                recommandation=f"Corriger les écritures {taux} → 10% ou 20%.",
                lignes_sources=gl[mask][["date","compte","libelle","debit","credit"]].head(20)
            ))

    # ── TEST 5 : TVA non déductible sur charges exclues ──
    for cpte, label in [("6185","Restauration"), ("6186","Cadeaux"), ("6187","Dons"), ("6135","Location véhicules")]:
        mask = gl["compte"].str.startswith(cpte)
        if mask.sum() == 0:
            continue
        montant = gl[mask]["debit"].sum()
        tva_est = montant * 0.20 / 1.20
        if tva_est > 200:
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code=f"TVA-005-{cpte}",
                severity="MOYEN", compte=cpte, montant=tva_est,
                description=f"{cpte} ({label}) : {montant:,.0f} MAD — TVA potentiellement non déductible",
                reference_cgi="Art. 106 CGI — Exclusions du droit à déduction TVA",
                recommandation=f"Vérifier TVA déduite sur {label} (cpte 34552). Si oui, réintégrer.",
                lignes_sources=_extraire_lignes(gl, [cpte])
            ))

    # ── TEST 6 : Crédit TVA récurrent ──
    if gl["compte"].str.startswith("3456").any():
        credit = _solde_compte(gl, "3456")
        if credit > 50000:
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code="TVA-006", severity="MOYEN",
                compte="3456", montant=credit,
                description=f"Crédit TVA cumulé : {credit:,.0f} MAD — signal de contrôle DGI",
                reference_cgi="Art. 103 CGI — Remboursement crédit TVA",
                recommandation="Vérifier éligibilité au remboursement ou opérations exonérées.",
                lignes_sources=_extraire_lignes(gl, ["3456"])
            ))

    # ── TEST 7 : Retenue source TVA LF2024 ──
    if exercice >= 2024:
        tva_ded = gl[gl["compte"].str.startswith("34552")]["debit"].sum()
        rts = gl[gl["compte"].str.startswith("4456")]["credit"].sum()
        if tva_ded > 10000 and rts == 0:
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code="TVA-007", severity="MOYEN",
                compte="4456", montant=tva_ded,
                description=f"Aucune retenue source TVA (4456) — TVA déductible = {tva_ded:,.0f} MAD",
                reference_cgi="Art. 117-V CGI — LF2024 retenue TVA applicable depuis 01/07/2024",
                recommandation="Vérifier si fournisseurs concernés font l'objet de RAS TVA.",
            ))

    # ── TEST 8 : Prorata activités mixtes ──
    ca_serv = gl[gl["compte"].str.match(r"^71[3-9]")]["credit"].sum()
    ca_march = gl[gl["compte"].str.match(r"^711")]["credit"].sum()
    if ca_serv > 0 and ca_march > 0:
        prorata = ca_march / (ca_march + ca_serv)
        if prorata < 0.95 and not gl["compte"].str.startswith("34553").any():
            anomalies.append(Anomalie(
                bloc="BLOC 1 - TVA", code="TVA-008", severity="MOYEN",
                compte="34553", montant=None,
                description=f"Activité mixte : CA services {ca_serv:,.0f} / CA marchandises {ca_march:,.0f}",
                reference_cgi="Art. 104 CGI — Règle du prorata pour déduction TVA",
                recommandation=f"Calculer le prorata ({prorata:.1%}) et ajuster via compte 34553.",
                detail=f"Prorata estimé : {prorata:.1%}"
            ))

    return anomalies
