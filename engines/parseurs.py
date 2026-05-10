"""
parseurs.py — Lecture standardisée de tous les fichiers sources
FEC (Sage/Ciel/tout logiciel), relevés bancaires multi-banques, TVA, 9421
"""
import pandas as pd
import numpy as np
import re
import io
from typing import Optional, Tuple, Dict
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════════
# FEC — 18 colonnes fixes (Art. A47 A-1 LPF)
# ════════════════════════════════════════════════════════════════

FEC_COLONNES = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib",
    "Debit", "Credit",
    "EcritureLet", "DateLet", "ValidDate", "Montantdevise", "Idevise"
]

# Colonnes indispensables pour l'analyse
FEC_CORE = ["CompteNum", "CompteLib", "EcritureLib", "Debit", "Credit",
            "EcritureDate", "JournalCode", "JournalLib", "EcritureNum",
            "PieceRef", "PieceDate"]


@dataclass
class ParseResult:
    """Résultat d'un parseur avec données et métadonnées."""
    df: pd.DataFrame
    source_type: str          # "fec", "releve", "tva", "9421", "excel_gl"
    nb_lignes: int = 0
    warnings: list = field(default_factory=list)
    erreur: Optional[str] = None

    def __post_init__(self):
        self.nb_lignes = len(self.df)


def lire_fec(filepath: str) -> ParseResult:
    """
    Parse un fichier FEC (.txt tab/pipe) OU un Grand Livre Excel/CSV.
    Accepte tous les formats : FEC Sage, Excel GL, CSV.
    """
    ext = str(filepath).rsplit(".", 1)[-1].lower()
    encodings = ["utf-8", "latin-1", "cp1252", "utf-8-sig"]
    df = None
    warnings = []
    is_true_fec = False

    if ext in ("txt", "csv"):
        for enc in encodings:
            try:
                with open(filepath, "r", encoding=enc, errors="replace") as fh:
                    premiere_ligne = fh.readline()
                sep = "|" if "|" in premiere_ligne else (
                    ";" if (";" in premiere_ligne and "\t" not in premiere_ligne) else "\t"
                )
                df_raw = pd.read_csv(filepath, sep=sep, encoding=enc,
                    dtype=str, on_bad_lines="skip", skipinitialspace=True, low_memory=False)
                cols_lower = [c.lower().strip() for c in df_raw.columns]
                if "comptenum" in cols_lower and ("debit" in cols_lower or "credit" in cols_lower):
                    df = df_raw
                    is_true_fec = True
                    break
                elif len(df_raw.columns) >= 3:
                    df = df_raw
                    break
            except Exception:
                continue

    elif ext in ("xlsx", "xls"):
        try:
            from engines.normalisation import detecter_header_row
            df_raw = pd.read_excel(filepath, header=None, dtype=str)
            hr = detecter_header_row(df_raw)
            df = pd.read_excel(filepath, header=hr, dtype=str)
            df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
            df = df.dropna(how="all").reset_index(drop=True)
        except Exception as e:
            return ParseResult(pd.DataFrame(), "fec", erreur=str(e))

    if df is None or df.empty:
        df = _lire_fichier_generique(filepath)

    if df is None or df.empty:
        return ParseResult(pd.DataFrame(), "fec", erreur="Impossible de lire le fichier")

    if is_true_fec:
        col_rename = {}
        for col in df.columns:
            for fec_col in FEC_COLONNES:
                if col.strip().lower() == fec_col.lower():
                    col_rename[col] = fec_col
                    break
        df = df.rename(columns=col_rename)
        for c in FEC_COLONNES:
            if c not in df.columns:
                df[c] = ""
        for col in ["Debit", "Credit"]:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.strip()
                .str.replace(r"\s","",regex=True).str.replace(",",".",regex=False)
                .str.replace("−","-",regex=False).str.replace(r"[^\d.\-]","",regex=True),
                errors="coerce").fillna(0.0)
        if "EcritureDate" in df.columns:
            df["EcritureDate"] = pd.to_datetime(
                df["EcritureDate"].astype(str).str.strip(), format="%Y%m%d", errors="coerce")
            mask_null = df["EcritureDate"].isna()
            if mask_null.any():
                df.loc[mask_null,"EcritureDate"] = pd.to_datetime(
                    df.loc[mask_null,"EcritureDate"].astype(str), dayfirst=True, errors="coerce")
        df = df[df["CompteNum"].astype(str).str.match(r"^\d{3,}")].copy()
        df["compte"]  = df["CompteNum"].astype(str).str.strip()
        df["libelle"] = df.get("EcritureLib", pd.Series([""] * len(df))).astype(str).fillna("")
        df["debit"]   = df["Debit"]
        df["credit"]  = df["Credit"]
        df["date"]    = df.get("EcritureDate", pd.NaT)
        df["journal"] = df.get("JournalCode", pd.Series([""] * len(df))).astype(str).fillna("")
    else:
        from engines.normalisation import normaliser_df_gl
        df = normaliser_df_gl(df)
        warnings.append("Lu comme Grand Livre (pas FEC natif) — colonnes FEC non disponibles.")

    df = df.reset_index(drop=True)
    if len(df) == 0:
        warnings.append("Aucune écriture valide trouvée")
    return ParseResult(df, "fec" if is_true_fec else "excel_gl", warnings=warnings)


def calculer_balance_depuis_fec(fec_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule la balance comptable (N ou N-1) directement depuis un FEC.
    Retourne un DataFrame avec : compte, libelle, total_debit, total_credit, solde
    """
    if fec_df.empty:
        return pd.DataFrame(columns=["compte", "libelle", "total_debit", "total_credit", "solde"])

    grp = fec_df.groupby("compte").agg(
        libelle=("libelle", "first"),
        total_debit=("debit", "sum"),
        total_credit=("credit", "sum")
    ).reset_index()

    grp["solde"] = grp["total_debit"] - grp["total_credit"]

    # Solde "normal" par classe
    # Classes 1,4 (passif) et 7 (produits) → solde créditeur = positif
    grp["solde_crediteur"] = 0.0
    grp["solde_debiteur"] = 0.0
    mask_credit = grp["compte"].str.match(r"^[147]")
    grp.loc[mask_credit, "solde_crediteur"] = (-grp.loc[mask_credit, "solde"]).clip(lower=0)
    grp.loc[~mask_credit, "solde_debiteur"] = grp.loc[~mask_credit, "solde"].clip(lower=0)

    return grp


def extraire_lignes_anomalie(fec_df: pd.DataFrame, comptes: list,
                              mots_cles: list = None, max_lignes: int = 20) -> pd.DataFrame:
    """
    Extrait les lignes FEC sources d'une anomalie.
    comptes : liste de préfixes de compte (ex: ["6582", "4455"])
    mots_cles : filtrer aussi par libellé
    """
    if fec_df.empty:
        return pd.DataFrame()

    mask = pd.Series([False] * len(fec_df))
    for cpte in comptes:
        mask |= fec_df["compte"].str.startswith(str(cpte))

    if mots_cles:
        mask_kw = pd.Series([False] * len(fec_df))
        for mot in mots_cles:
            mask_kw |= fec_df["libelle"].str.lower().str.contains(mot.lower(), na=False)
        mask = mask & mask_kw if comptes else mask_kw

    lignes = fec_df[mask].copy()

    # Colonnes à afficher dans le rapport
    cols = []
    for c in ["EcritureDate", "JournalCode", "EcritureNum", "compte", "CompteLib",
              "libelle", "PieceRef", "debit", "credit"]:
        if c in lignes.columns:
            cols.append(c)

    lignes = lignes[cols].head(max_lignes)

    # Formater la date
    if "EcritureDate" in lignes.columns:
        lignes["EcritureDate"] = lignes["EcritureDate"].dt.strftime("%d/%m/%Y").fillna("")

    return lignes.reset_index(drop=True)


# ════════════════════════════════════════════════════════════════
# RELEVÉ BANCAIRE — mapping multi-banques Maroc
# ════════════════════════════════════════════════════════════════

# Nomenclature des 6 principales banques marocaines
BANQUES_COLONNES = {
    "CIH Bank": {
        "date": ["date opération", "date operation", "date"],
        "libelle": ["libellé", "libelle", "détail opération", "detail operation"],
        "debit": ["débit", "debit"],
        "credit": ["crédit", "credit"],
        "solde": ["solde"],
    },
    "Attijariwafa Bank": {
        "date": ["date", "date opération", "date valeur"],
        "libelle": ["détail opération", "detail operation", "opération", "operation", "libellé"],
        "debit": ["montant débit", "montant debit", "débit", "debit"],
        "credit": ["montant crédit", "montant credit", "crédit", "credit"],
        "solde": ["solde", "solde (mad)"],
    },
    "BMCE / Bank of Africa": {
        "date": ["date", "date valeur", "date opération"],
        "libelle": ["description", "libellé", "nature opération"],
        "debit": ["sortie", "débit", "debit", "montant sortie"],
        "credit": ["entrée", "crédit", "credit", "montant entrée"],
        "solde": ["solde", "solde courant"],
    },
    "Banque Populaire": {
        "date": ["date", "date opération", "date valeur"],
        "libelle": ["opération", "libellé opération", "libellé"],
        "debit": ["débit (mad)", "débit", "debit"],
        "credit": ["crédit (mad)", "crédit", "credit"],
        "solde": ["solde (mad)", "solde"],
    },
    "BMCI": {
        "date": ["date", "date opération"],
        "libelle": ["libellé", "référence", "description"],
        "debit": ["débit mad", "débit", "debit mad"],
        "credit": ["crédit mad", "crédit", "credit mad"],
        "solde": ["solde", "solde mad"],
    },
    "Société Générale Maroc": {
        "date": ["date comptable", "date", "date opération"],
        "libelle": ["libellé opération", "libellé", "description"],
        "debit": ["débit", "montant débit", "débit (dhs)"],
        "credit": ["crédit", "montant crédit", "crédit (dhs)"],
        "solde": ["solde", "solde (dhs)"],
    },
    # Générique (fallback)
    "_generique": {
        "date": ["date", "date opération", "date valeur", "date comptable"],
        "libelle": ["libellé", "libelle", "description", "opération", "motif", "détail"],
        "debit": ["débit", "debit", "sortie", "paiement", "montant débit", "débit (mad)", "débit mad"],
        "credit": ["crédit", "credit", "entrée", "encaissement", "montant crédit", "crédit (mad)", "crédit mad"],
        "solde": ["solde", "solde (mad)", "solde mad", "balance"],
        "montant": ["montant", "amount"],
    }
}

# Construire un index unifié de tous les alias connus
_ALL_ALIASES: Dict[str, str] = {}
for banque_cfg in BANQUES_COLONNES.values():
    for target, aliases in banque_cfg.items():
        for alias in aliases:
            if alias not in _ALL_ALIASES:
                _ALL_ALIASES[alias.lower().strip()] = target


def lire_releve_bancaire(filepath: str) -> ParseResult:
    """
    Parse un relevé bancaire de n'importe quelle banque marocaine.
    Détecte automatiquement la structure des colonnes.
    """
    try:
        from app import lire_fichier_brut
        df_raw = lire_fichier_brut(filepath)
    except Exception:
        df_raw = _lire_fichier_generique(filepath)

    if df_raw is None or df_raw.empty:
        return ParseResult(pd.DataFrame(), "releve", erreur="Impossible de lire le relevé bancaire")

    warnings = []
    df = df_raw.copy()

    # Mapper les colonnes
    rename = {}
    used = set()
    for col in df.columns:
        col_key = str(col).lower().strip()
        target = _ALL_ALIASES.get(col_key)
        if target and target not in used and col not in rename:
            rename[col] = target
            used.add(target)

    df = df.rename(columns=rename)

    # Si on a une colonne "montant" unique (positif=crédit, négatif=débit)
    if "montant" in df.columns and "debit" not in df.columns:
        df["montant_num"] = pd.to_numeric(
            df["montant"].astype(str).str.replace(r"\s", "", regex=True)
            .str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True),
            errors="coerce"
        ).fillna(0)
        df["debit"] = df["montant_num"].clip(upper=0).abs()
        df["credit"] = df["montant_num"].clip(lower=0)

    # Normaliser débit/crédit
    for col in ["debit", "credit"]:
        if col not in df.columns:
            df[col] = 0.0
            warnings.append(f"Colonne '{col}' non trouvée dans le relevé")
        else:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"\s", "", regex=True)
                .str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True),
                errors="coerce"
            ).fillna(0.0)

    # Normaliser date
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")

    # Normaliser libellé
    if "libelle" not in df.columns:
        for col in df.columns:
            if col not in ["debit", "credit", "date", "solde", "montant"]:
                df["libelle"] = df[col].astype(str).fillna("")
                break
    if "libelle" not in df.columns:
        df["libelle"] = ""
    else:
        df["libelle"] = df["libelle"].astype(str).fillna("")

    # Supprimer lignes vides
    df = df[(df["debit"] != 0) | (df["credit"] != 0)].reset_index(drop=True)

    if len(df) == 0:
        warnings.append("Aucune opération trouvée dans le relevé (vérifier les colonnes débit/crédit)")

    return ParseResult(df, "releve", warnings=warnings)


# ════════════════════════════════════════════════════════════════
# DÉCLARATIONS TVA
# ════════════════════════════════════════════════════════════════

TVA_COLONNES_ACCEPTEES = {
    "periode": ["période", "periode", "mois", "trimestre", "period", "date", "exercice"],
    "base_taxable": ["base taxable", "base_taxable", "ca taxable", "ca ht", "chiffre d'affaires ht",
                     "montant ht", "base imposition", "base tva"],
    "tva_collectee": ["tva collectée", "tva_collectee", "tva facturée", "tva facturée 20%",
                      "tva collectee", "tva sur ventes", "tva débit"],
    "tva_deductible": ["tva déductible", "tva_deductible", "tva récupérable", "tva sur achats",
                       "tva crédit", "tva deductible"],
    "tva_nette": ["tva nette", "tva_nette", "tva à payer", "solde tva", "tva due", "tva net"],
}


def lire_declarations_tva(filepath: str) -> ParseResult:
    """Parse un fichier de déclarations TVA (export Sage ou template fourni)."""
    warnings = []
    try:
        df_raw = _lire_fichier_generique(filepath)
    except Exception as e:
        return ParseResult(pd.DataFrame(), "tva", erreur=str(e))

    if df_raw is None or df_raw.empty:
        return ParseResult(pd.DataFrame(), "tva", erreur="Fichier TVA vide")

    df = df_raw.copy()
    rename = {}
    used = set()

    for col in df.columns:
        col_key = str(col).lower().strip()
        for target, aliases in TVA_COLONNES_ACCEPTEES.items():
            if target in used:
                continue
            if any(alias.lower() == col_key or alias.lower() in col_key for alias in aliases):
                rename[col] = target
                used.add(target)
                break

    df = df.rename(columns=rename)

    for col in ["base_taxable", "tva_collectee", "tva_deductible", "tva_nette"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"\s", "", regex=True)
                .str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True),
                errors="coerce"
            ).fillna(0.0)
        else:
            df[col] = 0.0
            if col != "tva_nette":
                warnings.append(f"Colonne '{col}' non trouvée dans le fichier TVA")

    df = df.dropna(how="all").reset_index(drop=True)
    return ParseResult(df, "tva", warnings=warnings)


# ════════════════════════════════════════════════════════════════
# DÉCLARATION 9421
# ════════════════════════════════════════════════════════════════

D9421_COLONNES_ACCEPTEES = {
    "beneficiaire": ["bénéficiaire", "beneficiaire", "nom", "tiers", "prestataire", "raison sociale"],
    "ice_cin": ["ice", "cin", "identifiant", "ice / cin", "n° ice", "n° cin"],
    "nature": ["nature", "nature prestation", "prestation", "service", "objet"],
    "montant_brut": ["montant brut", "montant_brut", "montant", "honoraires", "rémunération", "remuneration"],
    "retenue_ir": ["retenue ir", "retenue_ir", "ras ir", "retenue source ir", "ir retenu",
                   "retenue à la source", "retenue 30%"],
    "montant_net": ["montant net", "montant_net", "net versé", "net paye"],
}


def lire_declaration_9421(filepath: str) -> ParseResult:
    """Parse une déclaration 9421 (rémunérations versées à des tiers)."""
    warnings = []
    try:
        df_raw = _lire_fichier_generique(filepath)
    except Exception as e:
        return ParseResult(pd.DataFrame(), "9421", erreur=str(e))

    if df_raw is None or df_raw.empty:
        return ParseResult(pd.DataFrame(), "9421", erreur="Fichier 9421 vide")

    df = df_raw.copy()
    rename = {}
    used = set()

    for col in df.columns:
        col_key = str(col).lower().strip()
        for target, aliases in D9421_COLONNES_ACCEPTEES.items():
            if target in used:
                continue
            if any(alias.lower() == col_key or alias.lower() in col_key for alias in aliases):
                rename[col] = target
                used.add(target)
                break

    df = df.rename(columns=rename)

    for col in ["montant_brut", "retenue_ir", "montant_net"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(r"\s", "", regex=True)
                .str.replace(",", ".").str.replace(r"[^\d.\-]", "", regex=True),
                errors="coerce"
            ).fillna(0.0)
        else:
            df[col] = 0.0

    df = df.dropna(how="all").reset_index(drop=True)
    return ParseResult(df, "9421", warnings=warnings)


# ════════════════════════════════════════════════════════════════
# BILAN / CPC / ESG (liasse fiscale DGI)
# ════════════════════════════════════════════════════════════════

def lire_liasse_fiscale(filepath: str) -> Tuple[dict, list]:
    """
    Tente de lire un fichier de liasse fiscale (Bilan + CPC + ESG).
    Retourne un dict {feuille: DataFrame} et une liste de warnings.
    """
    warnings = []
    resultats = {}
    try:
        xl = pd.ExcelFile(filepath)
        for sheet in xl.sheet_names:
            nom = sheet.lower().strip()
            df = xl.parse(sheet, dtype=str)
            if any(k in nom for k in ["bilan", "actif", "passif"]):
                resultats["bilan"] = df
            elif any(k in nom for k in ["cpc", "résultat", "resultat", "compte de produits"]):
                resultats["cpc"] = df
            elif "esg" in nom or "soldes" in nom:
                resultats["esg"] = df
            elif any(k in nom for k in ["is", "fiscal", "liasse"]):
                resultats["is_fiscal"] = df
            else:
                # Détecter par contenu
                cols_lower = [str(c).lower() for c in df.columns]
                if any("chiffre d'affaires" in c or "produits d'exploitation" in c for c in cols_lower):
                    resultats.setdefault("cpc", df)
                elif any("immobilisations" in c or "actif immobilisé" in c for c in cols_lower):
                    resultats.setdefault("bilan", df)
    except Exception as e:
        warnings.append(f"Liasse fiscale : {e}")

    return resultats, warnings


# ════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ════════════════════════════════════════════════════════════════

def _lire_fichier_generique(filepath: str) -> Optional[pd.DataFrame]:
    """Lit Excel ou CSV avec détection d'encodage et de header."""
    from engines.normalisation import detecter_header_row

    ext = str(filepath).rsplit(".", 1)[-1].lower()
    encodings = ["utf-8", "latin-1", "cp1252", "utf-8-sig"]

    try:
        if ext in ("xlsx", "xls"):
            df_raw = pd.read_excel(filepath, header=None, dtype=str)
            header_row = detecter_header_row(df_raw)
            df = pd.read_excel(filepath, header=header_row, dtype=str)
        else:
            df_raw = None
            enc_used = "utf-8"
            for enc in encodings:
                try:
                    df_raw = pd.read_csv(
                        filepath, header=None, encoding=enc,
                        sep=None, engine="python", dtype=str
                    )
                    enc_used = enc
                    break
                except Exception:
                    continue
            if df_raw is None:
                return None
            from engines.normalisation import detecter_header_row
            header_row = detecter_header_row(df_raw)
            df = pd.read_csv(
                filepath, header=header_row, encoding=enc_used,
                sep=None, engine="python", dtype=str
            )

        # Nettoyer
        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
        df = df.dropna(how="all").reset_index(drop=True)

        # Supprimer lignes totaux
        if len(df.columns) > 0:
            fc = df.columns[0]
            mots = {"total", "sous-total", "total général", "total general", "nan", "", "none"}
            df = df[~df[fc].astype(str).str.lower().str.strip().isin(mots)]

        return df.reset_index(drop=True)

    except Exception:
        return None
