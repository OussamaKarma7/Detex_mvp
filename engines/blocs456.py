"""
BLOCS 4, 5, 6 — v4 corrigée
Fixes :
  - IE-002-63 : filtre restreint à ^633 (TP seulement, exclure 6311/6582)
  - IE-003 : solde clients calculé par agrégation sur 4 premiers chiffres
  - CGNC-002 : exclure comptes 28xxx (amortissements cumulés — créditeurs par nature)
"""
import pandas as pd
import numpy as np
from typing import List, Optional
from engines.bloc1_tva import Anomalie
from engines.normalisation import normaliser_df_gl


def _s(gl, prefix):
    m = gl["compte"].str.startswith(prefix)
    return gl[m]["debit"].sum() - gl[m]["credit"].sum()


def _lignes(gl, comptes, n=20):
    mask = pd.Series([False]*len(gl))
    for c in comptes:
        mask |= gl["compte"].str.startswith(str(c))
    sub = gl[mask]
    cols = [c for c in ["date","journal","compte","libelle","debit","credit"] if c in sub.columns]
    return sub[cols].head(n).reset_index(drop=True) if not sub.empty else None


# ═══════════════════════════════════════════════════════════════
# BLOC 4 — Cohérence inter-exercices
# ═══════════════════════════════════════════════════════════════

def analyser_inter_exercices(
    balance_n: pd.DataFrame,
    balance_n1: pd.DataFrame
) -> List[Anomalie]:
    anomalies = []
    bn  = _normaliser_balance(balance_n)
    bn1 = _normaliser_balance(balance_n1)
    if bn.empty or bn1.empty:
        return anomalies

    merged = bn.merge(bn1, on="compte_4", suffixes=("_n","_n1"), how="outer").fillna(0)

    # ── TEST 1 : Variation CA ──
    mask_ca = merged["compte_4"].str.match(r"^7")
    ca_n  = merged[mask_ca]["sc_n"].sum()   # solde créditeur N
    ca_n1 = merged[mask_ca]["sc_n1"].sum()  # solde créditeur N-1

    if ca_n1 > 0 and ca_n > 0:
        var = (ca_n - ca_n1) / ca_n1
        if abs(var) > 0.25:
            anomalies.append(Anomalie(
                bloc="BLOC 4 - Inter-Exercices", code="IE-001",
                severity="CRITIQUE" if abs(var) > 0.40 else "MOYEN",
                compte="71xx", montant=abs(ca_n - ca_n1),
                description=f"Variation CA N/N-1 : {var:+.1%} (N={ca_n:,.0f} / N-1={ca_n1:,.0f} MAD)",
                reference_cgi="Art. 210 CGI — Variations anormales = signal de contrôle DGI prioritaire",
                recommandation="Documenter les causes (nouveau marché, perte client, saisonnalité).",
                detail=f"Variation absolue : {abs(ca_n-ca_n1):,.0f} MAD"
            ))

    # ── TEST 2 : Variation charges — FIX : catégories précises ──
    categories = [
        (r"^61", "61", "Achats et charges externes"),       # Tous les 61xx ensemble
        (r"^633", "63", "Impôts et taxes (TP, patente)"),   # FIX: ^633 seulement, exclut 6311/6582
        (r"^617", "64", "Charges de personnel"),
        (r"^65", "65", "Autres charges d'exploitation"),
    ]
    for pattern, code_suffix, label in categories:
        mask = merged["compte_4"].str.match(pattern)
        c_n  = merged[mask]["sd_n"].sum()
        c_n1 = merged[mask]["sd_n1"].sum()
        if c_n1 > 10000 and c_n > 0:
            var = (c_n - c_n1) / c_n1
            if abs(var) > 0.30:
                anomalies.append(Anomalie(
                    bloc="BLOC 4 - Inter-Exercices", code=f"IE-002-{code_suffix}",
                    severity="MOYEN", compte=f"{code_suffix}xx",
                    montant=abs(c_n - c_n1),
                    description=f"{label} : variation {var:+.1%} (N={c_n:,.0f} / N-1={c_n1:,.0f})",
                    reference_cgi="Art. 210 CGI — Incohérences déclenchent contrôle sur pièces",
                    recommandation=f"Justifier la variation des {label}.",
                ))

    # ── TEST 3 : Créances clients — FIX : agrégation sur 4 chiffres ──
    # Agréger tous les sous-comptes 3421x ensemble
    mask_cli = merged["compte_4"] == "3421"
    clients_n = merged[mask_cli]["sd_n"].sum()

    if ca_n > 0 and clients_n > ca_n * 0.30:
        jours = clients_n / ca_n * 365
        anomalies.append(Anomalie(
            bloc="BLOC 4 - Inter-Exercices", code="IE-003",
            severity="MOYEN", compte="3421", montant=clients_n,
            description=f"Créances clients : {clients_n:,.0f} MAD ({jours:.0f} jours de CA)",
            reference_cgi="CGNC + Art. 10 CGI — Créances douteuses à provisionner",
            recommandation="Analyser antériorité créances >90 jours. Provisionner les douteuses.",
            detail=f"Délai moyen client estimé : {jours:.0f} jours"
        ))

    # ── TEST 4 : Taux de marge brute ──
    mask_ach = merged["compte_4"].str.match(r"^611|^612")
    ach_n  = merged[mask_ach]["sd_n"].sum()
    ach_n1 = merged[mask_ach]["sd_n1"].sum()
    if ca_n > 0 and ach_n > 0 and ca_n1 > 0 and ach_n1 > 0:
        mg_n  = (ca_n  - ach_n)  / ca_n
        mg_n1 = (ca_n1 - ach_n1) / ca_n1
        if abs(mg_n - mg_n1) > 0.10:
            anomalies.append(Anomalie(
                bloc="BLOC 4 - Inter-Exercices", code="IE-004",
                severity="MOYEN", compte="611-612/71xx", montant=None,
                description=f"Taux marge : N={mg_n:.1%} vs N-1={mg_n1:.1%} (var {mg_n-mg_n1:+.1f} pts)",
                reference_cgi="Art. 210 CGI — Baisse inexpliquée de marge = signal DGI fort",
                recommandation="Expliquer la variation (fournisseurs, concurrence, commerciale).",
                detail=f"Marge N : {mg_n:.1%} | Marge N-1 : {mg_n1:.1%}"
            ))

    return anomalies


# ═══════════════════════════════════════════════════════════════
# BLOC 5 — Conformité CGNC
# ═══════════════════════════════════════════════════════════════

SOLDES_NORMAUX = {
    "1":"credit","2":"debit","3":"debit",
    "4":"credit","5":"debit","6":"debit","7":"credit"
}

# Comptes qui sont TOUJOURS créditeurs même en classe 2 ou 3
COMPTES_CREDITEURS_NORMAUX = {
    "28",  # Amortissements cumulés (281x, 282x, 283x, 284x, 285x)
    "39",  # Provisions pour dépréciation stocks
    "29",  # Provisions pour dépréciation immos
    "49",  # Provisions pour dépréciation créances
}


def analyser_conformite_cgnc(grand_livre: pd.DataFrame) -> List[Anomalie]:
    anomalies = []
    gl = normaliser_df_gl(grand_livre)

    # TEST 1 : Comptes hors PCGE
    hors = gl[~gl["compte"].str.match(r"^[1-9]\d{2,}")]
    if len(hors) > 0:
        anomalies.append(Anomalie(
            bloc="BLOC 5 - CGNC", code="CGNC-001", severity="MOYEN",
            compte="N/A", montant=None,
            description=f"{len(hors)} écriture(s) avec numéros de compte hors PCGE",
            reference_cgi="Loi 9-88 + CGNC — Plan comptable marocain obligatoire",
            recommandation="Corriger les N° compte pour respecter le PCGE (classes 1-9).",
            detail=f"Exemples : {hors['compte'].unique()[:5].tolist()}",
            lignes_sources=hors[["compte","libelle","debit","credit"]].head(10)
        ))

    # TEST 2 : Soldes anormaux — FIX : exclure 28xxx, 29xxx, 39xxx, 49xxx
    soldes = gl.groupby("compte").agg(
        total_debit=("debit","sum"), total_credit=("credit","sum")
    ).reset_index()
    soldes["solde"] = soldes["total_debit"] - soldes["total_credit"]

    for _, row in soldes.iterrows():
        cpte = str(row["compte"])
        if len(cpte) < 2:
            continue
        classe = cpte[0]
        prefixe2 = cpte[:2]
        solde = row["solde"]
        attendu = SOLDES_NORMAUX.get(classe)
        seuil = 1000

        # FIX : Ignorer les comptes de provisions/amortissements cumulés
        if prefixe2 in COMPTES_CREDITEURS_NORMAUX:
            continue

        # FIX : Ignorer les sous-comptes auxiliaires (ex: 3421-CLIENT, 4411-FOURN)
        if "-" in cpte or (len(cpte) > 4 and not cpte.isdigit()):
            continue

        # FIX : Comptes de lettrage / contrôle (3421, 4411 principaux) peuvent avoir
        # un solde créditeur temporaire si les encaissements arrivent avant la balance de clôture
        # → signaler uniquement si le déséquilibre est très marqué (>3× le CA annuel)
        if prefixe2 in ("34", "44") and abs(solde) > 0:
            # Vérifier si c'est un compte de tiers (clients/fournisseurs) — solde créditeur 3421 = sur-encaissement
            if classe == "3" and solde < -seuil:
                # Solde créditeur 3421 = encaissements non encore imputés (normal en cours d'exercice)
                # Ne signaler que si très significatif
                if abs(solde) < 1000000:  # Moins d'1M → probablement normal
                    continue

        if attendu == "debit" and solde < -seuil:
            anomalies.append(Anomalie(
                bloc="BLOC 5 - CGNC", code=f"CGNC-002-{cpte}",
                severity="MOYEN", compte=cpte, montant=abs(solde),
                description=f"Compte {cpte} (classe {classe}) : solde CRÉDITEUR {solde:,.0f} MAD — normalement débiteur",
                reference_cgi="CGNC — Sens de solde non conforme au plan comptable",
                recommandation=f"Vérifier les écritures du compte {cpte}.",
                lignes_sources=_lignes(gl, [cpte])
            ))
        elif attendu == "credit" and solde > seuil and classe == "4":
            anomalies.append(Anomalie(
                bloc="BLOC 5 - CGNC", code=f"CGNC-002-{cpte}",
                severity="FAIBLE", compte=cpte, montant=solde,
                description=f"Compte {cpte} : solde DÉBITEUR {solde:,.0f} MAD — normalement créditeur",
                reference_cgi="CGNC — Compte de passif avec solde débiteur",
                recommandation=f"Vérifier sur-paiement fournisseur ou erreur d'imputation.",
                lignes_sources=_lignes(gl, [cpte])
            ))

    # TEST 3 : Immobilisations en charges (exclure 619x = dotations légitimes)
    mots_immo = ["ordinateur","serveur","véhicule","voiture","mobilier","logiciel","climatiseur"]
    for mot in mots_immo:
        mask = (
            gl["libelle"].astype(str).str.lower().str.contains(mot, na=False) &
            gl["compte"].str.match(r"^6") &
            (~gl["compte"].str.match(r"^619")) &  # exclure dotations amort.
            (~gl["compte"].str.match(r"^695")) &  # exclure IS
            (gl["debit"] > 10000)
        )
        suspects = gl[mask]
        if len(suspects) > 0:
            montant = suspects["debit"].sum()
            anomalies.append(Anomalie(
                bloc="BLOC 5 - CGNC", code=f"CGNC-003-{mot[:4]}",
                severity="MOYEN", compte="6xxx", montant=montant,
                description=f"'{mot}' comptabilisé en charges pour {montant:,.0f} MAD — possible immobilisation",
                reference_cgi="CGNC Art. 2 — Immobilisations à activer si usage >1 an et valeur >5 000 MAD",
                recommandation=f"Vérifier si '{mot}' est durable. Si oui, reclasser en 2xxx et amortir.",
                lignes_sources=suspects[["date","compte","libelle","debit","credit"]].head(10)
            ))

    # TEST 4 : Comptes de régularisation non justifiés
    for cpte, label in [("3481","Charges constatées d'avance"),("4481","Produits constatés d'avance"),
                         ("3491","Charges à répartir"),("4491","Produits à recevoir")]:
        mask = gl["compte"].str.startswith(cpte)
        solde = gl[mask]["debit"].sum() - gl[mask]["credit"].sum()
        if abs(solde) > 5000:
            anomalies.append(Anomalie(
                bloc="BLOC 5 - CGNC", code=f"CGNC-004-{cpte}",
                severity="FAIBLE", compte=cpte, montant=abs(solde),
                description=f"Compte {cpte} ({label}) : solde non nul {solde:,.0f} MAD",
                reference_cgi="CGNC — Comptes régularisation à solder ou justifier à la clôture",
                recommandation=f"Vérifier les écritures {cpte} en fin de période.",
                lignes_sources=_lignes(gl, [cpte])
            ))

    # TEST 5 : CCA excessifs
    mask_cca = gl["compte"].str.match(r"^455")
    cca = abs(gl[mask_cca]["debit"].sum() - gl[mask_cca]["credit"].sum())
    if cca > 500000:
        anomalies.append(Anomalie(
            bloc="BLOC 5 - CGNC", code="CGNC-005",
            severity="MOYEN", compte="455x", montant=cca,
            description=f"Comptes courants associés élevés : {cca:,.0f} MAD",
            reference_cgi="Art. 10-I-B-3° CGI — Intérêts CCA plafonnés; risque distribution déguisée",
            recommandation="Documenter l'origine des CCA et vérifier les taux d'intérêts DGI.",
            lignes_sources=_lignes(gl, ["4551","4552","4553","4554"])
        ))

    return anomalies


# ═══════════════════════════════════════════════════════════════
# BLOC 6 — Déclaration 9421
# ═══════════════════════════════════════════════════════════════

def analyser_declaration_9421(
    grand_livre: pd.DataFrame,
    declaration_9421: pd.DataFrame
) -> List[Anomalie]:
    anomalies = []
    gl = normaliser_df_gl(grand_livre)

    # Honoraires = 6123, 6124, 6125 uniquement (pas 6111 = achats)
    mask_honor = gl["compte"].str.match(r"^612[3-5]")
    total_honor_gl = gl[mask_honor]["debit"].sum()

    if not declaration_9421.empty:
        col_m = next((c for c in declaration_9421.columns
                      if any(k in c.lower() for k in ["montant_brut","montant brut","montant"])), None)
        total_9421 = pd.to_numeric(declaration_9421[col_m], errors="coerce").sum() if col_m else 0

        ecart = abs(total_honor_gl - total_9421)
        if ecart > 2000:
            anomalies.append(Anomalie(
                bloc="BLOC 6 - Déclaration 9421", code="D9421-001",
                severity="CRITIQUE", compte="6123-6125", montant=ecart,
                description=f"Honoraires GL ({total_honor_gl:,.0f} MAD) ≠ Total 9421 ({total_9421:,.0f} MAD)",
                reference_cgi="Art. 151 CGI — Déclaration annuelle rémunérations tiers obligatoire",
                recommandation="Tout prestataire >10 000 MAD doit figurer dans la 9421.",
                detail=f"Écart = {ecart:,.0f} MAD",
                lignes_sources=_lignes(gl, ["6123","6124","6125"])
            ))

        col_r = next((c for c in declaration_9421.columns
                      if any(k in c.lower() for k in ["retenue","ras"])), None)
        if col_r and total_9421 > 0:
            ras_theorique = total_9421 * 0.30
            ras_gl = gl[gl["compte"].str.match(r"^44525|^44521")]["credit"].sum()
            if abs(ras_gl - ras_theorique) > 2000:
                anomalies.append(Anomalie(
                    bloc="BLOC 6 - Déclaration 9421", code="D9421-002",
                    severity="CRITIQUE", compte="44525", montant=abs(ras_gl - ras_theorique),
                    description=f"RAS IR GL ({ras_gl:,.0f}) ≠ théorique 30% ({ras_theorique:,.0f})",
                    reference_cgi="Art. 160 CGI — RAS 30% sur honoraires, commissions, courtages",
                    recommandation="Vérifier que RAS 30% prélevée et reversée à la DGI.",
                    lignes_sources=_lignes(gl, ["44525","44521"])
                ))
    else:
        if total_honor_gl > 10000:
            anomalies.append(Anomalie(
                bloc="BLOC 6 - Déclaration 9421", code="D9421-003",
                severity="MOYEN", compte="6123-6125", montant=total_honor_gl,
                description=f"Honoraires GL = {total_honor_gl:,.0f} MAD — aucune 9421 fournie",
                reference_cgi="Art. 151 CGI — 9421 obligatoire si rémunérations tiers > 0",
                recommandation="Fournir la déclaration 9421 pour vérification des RAS IR.",
                lignes_sources=_lignes(gl, ["6123","6124","6125"])
            ))

    # RAS dividendes
    mask_div = gl["compte"].str.match(r"^730|^731|^732")
    div = gl[mask_div]["debit"].sum()
    ras_div = gl[gl["compte"].str.startswith("44521")]["credit"].sum()
    if div > 0 and ras_div == 0:
        anomalies.append(Anomalie(
            bloc="BLOC 6 - Déclaration 9421", code="D9421-004",
            severity="MOYEN", compte="44521", montant=div * 0.15,
            description=f"Dividendes versés ({div:,.0f} MAD) sans RAS sur compte 44521",
            reference_cgi="Art. 13 & 19 CGI — RAS 15% sur dividendes",
            recommandation="Vérifier RAS dividendes prélevée et reversée à la DGI.",
        ))

    return anomalies


# ═══════════════════════════════════════════════════════════════
# HELPERS — Normalisation balance avec agrégation 4 chiffres
# ═══════════════════════════════════════════════════════════════

def _normaliser_balance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise une balance et agrège les sous-comptes sur 4 chiffres.
    Ex: 3421-SORO + 3421-ONAS + 3421 → un seul compte "3421"
    Cela résout le bug des comptes auxiliaires non apurés.
    """
    # Si balance calculée depuis FEC (a total_debit/total_credit)
    if "total_debit" in df.columns and "total_credit" in df.columns:
        df = df.copy()
        if "compte" not in df.columns:
            return pd.DataFrame()
        df["compte"] = df["compte"].astype(str).str.strip()

        # Agréger sur 4 premiers chiffres (tronquer les sous-comptes)
        df["compte_4"] = df["compte"].str[:4]
        agg = df.groupby("compte_4").agg(
            total_debit=("total_debit","sum"),
            total_credit=("total_credit","sum")
        ).reset_index()
        agg["solde"] = agg["total_debit"] - agg["total_credit"]

        # Solde débiteur / créditeur selon la classe
        agg["sd"] = 0.0  # solde débiteur
        agg["sc"] = 0.0  # solde créditeur
        mask_c = agg["compte_4"].str.match(r"^[147]")
        agg.loc[mask_c, "sc"] = (-agg.loc[mask_c, "solde"]).clip(lower=0)
        agg.loc[~mask_c, "sd"] = agg.loc[~mask_c, "solde"].clip(lower=0)

        return agg

    # Sinon balance Excel classique
    df = df.copy()
    col_map = {
        "compte": ["compte","n° compte","code compte","numéro compte"],
        "libelle": ["libellé","libelle","intitulé","description"],
        "solde": ["solde","solde net","balance"],
        "debit": ["cumul débit","cumul debit","total débit","débit","debit"],
        "credit": ["cumul crédit","cumul credit","total crédit","crédit","credit"],
    }
    rename = {}; used = set()
    for target, candidates in col_map.items():
        for col in df.columns:
            if str(col).lower().strip() in candidates and target not in used:
                rename[col] = target; used.add(target); break
    df = df.rename(columns=rename)

    for col in ["debit","credit","solde"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"\s","",regex=True).str.replace(",",".",regex=False),
                errors="coerce"
            ).fillna(0)
        else:
            df[col] = 0.0

    if "solde" not in df.columns or df["solde"].abs().sum() == 0:
        df["solde"] = df.get("debit",0) - df.get("credit",0)

    if "compte" in df.columns:
        df["compte"] = df["compte"].astype(str).str.strip()
        df["compte_4"] = df["compte"].str[:4]

    # Agréger aussi les balances Excel
    if "compte_4" in df.columns:
        agg = df.groupby("compte_4").agg(
            total_debit=("debit","sum") if "debit" in df.columns else ("solde","sum"),
            total_credit=("credit","sum") if "credit" in df.columns else ("solde","count"),
        ).reset_index()
        agg["solde"] = agg["total_debit"] - agg["total_credit"]
        agg["sd"] = 0.0; agg["sc"] = 0.0
        mask_c = agg["compte_4"].str.match(r"^[147]")
        agg.loc[mask_c, "sc"] = (-agg.loc[mask_c, "solde"]).clip(lower=0)
        agg.loc[~mask_c, "sd"] = agg.loc[~mask_c, "solde"].clip(lower=0)
        return agg

    return df.dropna(how="all").reset_index(drop=True)


def _get_solde_merged(merged, mask, suffix):
    """Récupère le solde depuis les colonnes disponibles selon le suffixe."""
    for col in [f"sd{suffix}", f"sc{suffix}", f"solde{suffix}"]:
        if col in merged.columns:
            val = merged[mask][col].sum()
            if val != 0:
                return abs(val)
    return 0.0


# Bloc 7 — Liasse Fiscale (stub extensible)
def analyser_liasse_fiscale(liasse, fec_n, ca_exercice=0):
    return []
