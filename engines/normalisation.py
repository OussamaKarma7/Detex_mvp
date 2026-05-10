"""
Normalisation robuste des fichiers comptables marocains.
Gère tous les formats : Sage 100, Ciel, exports manuels, avec ou sans N° compte.
"""
import pandas as pd
import re
from typing import Optional

# ── Mapping libellé → numéro compte PCGE Maroc ──
LIBELLE_VERS_COMPTE = {
    "ventes marchandises": "7111",
    "ventes de marchandises": "7111",
    "vente marchandises": "7111",
    "chiffre d'affaires": "7111",
    "prestations de services": "7113",
    "ventes services": "7113",
    "services rendus": "7113",
    "produits accessoires": "7124",
    "tva collectée": "4455",
    "tva facturée": "4455",
    "tva sur ventes": "4455",
    "tva sur ca": "4455",
    "tva récupérable": "34552",
    "tva récupérable sur charges": "34552",
    "tva récup charges": "34552",
    "tva récupérable immo": "34551",
    "tva sur achats": "34552",
    "crédit de tva": "3456",
    "achats marchandises": "6111",
    "achats de marchandises": "6111",
    "achat marchandises": "6111",
    "achats matières premières": "6121",
    "matières premières": "6121",
    "services extérieurs": "6131",
    "loyers": "6131",
    "location": "6131",
    "autres charges externes": "6141",
    "transports": "6142",
    "honoraires": "6123",
    "commissions": "6123",
    "frais de personnel": "6171",
    "charges de personnel": "6171",
    "salaires": "6171",
    "traitements et salaires": "6171",
    "charges sociales": "6174",
    "cotisations sociales": "6174",
    "dotations amortissements": "6193",
    "amortissements": "6193",
    "dotations": "6193",
    "provisions risques": "6195",
    "provisions": "6195",
    "amendes et pénalités": "6582",
    "amendes": "6582",
    "pénalités": "6582",
    "pénalités fiscales": "6582",
    "dons et libéralités": "6187",
    "dons": "6187",
    "intérêts cca": "6311",
    "intérêts comptes courants": "6311",
    "intérêts sur emprunts": "6311",
    "impôts et taxes": "6331",
    "taxe professionnelle": "6331",
    "banque": "5141",
    "caisse": "5161",
    "clients": "3421",
    "créances clients": "3421",
    "fournisseurs": "4411",
    "dettes fournisseurs": "4411",
    "capital social": "1111",
    "capital": "1111",
    "report à nouveau": "1191",
    "résultat": "1191",
    "résultat net": "1191",
    "immobilisations": "2340",
    "matériel informatique": "2350",
    "matériel de bureau": "2410",
    "matériel transport": "2430",
    "véhicule de tourisme": "2431",
    "véhicules": "2430",
    "compte courant associé": "4551",
    "comptes courants associés": "4551",
    "cca": "4551",
    "is à payer": "4452",
    "impôt sur les sociétés": "4452",
    "tva à payer": "4455",
    "retenue source tva": "4456",
}

HEADER_KEYWORDS = {
    "compte", "libellé", "libelle", "débit", "debit", "crédit", "credit",
    "date", "journal", "solde", "montant", "description", "intitulé", "intitule",
    "cumul", "balance", "amount", "account", "tiers", "période", "periode",
    "base", "taxable", "tva", "retenue", "bénéficiaire", "beneficiaire",
    "n° compte", "numero", "mouvement", "écriture", "ecriture", "piece",
    "pièce", "reference", "référence", "nature", "sens",
}


def detecter_header_row(df_raw: pd.DataFrame) -> int:
    for i, row in df_raw.iterrows():
        valeurs = [str(v).lower().strip() for v in row.values
                   if pd.notna(v) and str(v).strip() not in ("", "nan")]
        matches = sum(1 for v in valeurs if any(k in v for k in HEADER_KEYWORDS))
        if matches >= 2:
            return i
    return 0


def mapper_compte_depuis_libelle(val: str) -> str:
    """Trouve le numéro PCGE le plus proche pour un libellé donné."""
    v = str(val).lower().strip()
    # Chercher le pattern le plus long qui correspond
    best_match = None
    best_len = 0
    for pattern, cpte in LIBELLE_VERS_COMPTE.items():
        if pattern in v and len(pattern) > best_len:
            best_match = cpte
            best_len = len(pattern)
    if best_match:
        return best_match
    # Chercher un numéro dans la valeur
    m = re.search(r'\b\d{3,6}\b', v)
    if m:
        return m.group()
    return "9999"


def normaliser_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise un DataFrame comptable vers les colonnes standards:
    compte, libelle, debit, credit, date (optionnel), journal (optionnel)

    Gère:
    - Fichiers Sage avec N° compte explicite
    - Fichiers sans N° compte (libellé seulement) → mapping PCGE
    - Colonnes en français avec accents, MAD, espaces
    """
    df = df.copy()

    # ── 1. Identifier et renommer les colonnes ──
    col_map = {
        "compte": [
            "compte", "n° compte", "no compte", "numero compte", "numéro compte",
            "account", "code compte", "cpte", "n°cpt", "n°cpte", "compte général",
            "compte general", "n° cpte", "code", "cpt",
        ],
        "libelle": [
            "libellé", "libelle", "description", "intitulé", "intitule",
            "description opération", "description operation", "opération", "operation",
            "écriture", "ecriture", "wording", "narration", "libellé du compte",
            "libelle du compte", "nature opération", "nature operation",
        ],
        "debit": [
            "débit", "debit", "débit (mad)", "debit (mad)", "montant débit",
            "montant debit", "mvt débit", "cumul débit", "debit mad", "mouvements débiteurs",
            "flux débit", "débit mad",
        ],
        "credit": [
            "crédit", "credit", "crédit (mad)", "credit (mad)", "montant crédit",
            "montant credit", "mvt crédit", "cumul crédit", "credit mad",
            "mouvements créditeurs", "flux crédit", "crédit mad",
        ],
        "date": [
            "date", "date écriture", "date ecriture", "date opération",
            "date operation", "date pièce", "date piece", "date comptable", "date valeur",
        ],
        "journal": ["journal", "code journal", "jrn", "jnl"],
    }

    rename = {}
    used_targets = set()
    for target, candidates in col_map.items():
        if target in used_targets:
            continue
        for col in df.columns:
            col_clean = str(col).lower().strip()
            if col_clean in candidates or any(c == col_clean for c in candidates):
                if col not in rename:
                    rename[col] = target
                    used_targets.add(target)
                    break

    df = df.rename(columns=rename)

    # ── 2. Normaliser débit/crédit ──
    for col in ["debit", "credit"]:
        if col not in df.columns:
            df[col] = 0.0
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0.0)  # Déjà numérique (FEC parsé)
        else:
            df[col] = (
                df[col].astype(str)
                .str.replace(r"\s", "", regex=True)
                .str.replace(",", ".", regex=False)
                .str.replace("−", "-", regex=False)
                .str.replace(r"[^\d.\-]", "", regex=True)
            )
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # ── 3. Gérer la colonne compte ──
    if "compte" not in df.columns:
        # Pas de colonne compte : chercher une colonne avec des numéros
        for col in df.columns:
            if col in ["debit", "credit", "date", "journal", "libelle"]:
                continue
            sample = df[col].dropna().astype(str).str.strip()
            pct_num = sample.str.match(r"^\d{3,}").mean() if len(sample) > 0 else 0
            if pct_num > 0.5:
                df = df.rename(columns={col: "compte"})
                break

    # ── 4. Vérifier si la colonne compte contient de vrais numéros ──
    if "compte" in df.columns:
        sample = df["compte"].dropna().astype(str).str.strip()
        pct_num = sample.str.match(r"^\d{3,}").mean() if len(sample) > 0 else 0

        if pct_num < 0.3:
            # La colonne "compte" contient des libellés, pas des numéros
            # → la renommer en libelle et mapper les comptes
            if "libelle" not in df.columns:
                df = df.rename(columns={"compte": "libelle"})
            df["compte"] = df["libelle"].apply(mapper_compte_depuis_libelle)
        else:
            df["compte"] = df["compte"].astype(str).str.strip().str.replace(r"\s+", "", regex=True)
    else:
        # Dernier recours : mapper depuis libelle
        if "libelle" in df.columns:
            df["compte"] = df["libelle"].apply(mapper_compte_depuis_libelle)
        else:
            df["compte"] = "9999"

    # ── 5. Libellé ──
    if "libelle" not in df.columns:
        # Chercher une colonne texte descriptive
        for col in df.columns:
            if col not in ["compte", "debit", "credit", "date", "journal"]:
                df["libelle"] = df[col].astype(str).fillna("")
                break
        else:
            df["libelle"] = ""
    else:
        df["libelle"] = df["libelle"].astype(str).fillna("")

    # ── 6. Supprimer lignes totalement vides ou parasites ──
    df = df.dropna(subset=["debit", "credit"], how="all")
    df = df[(df["debit"] != 0) | (df["credit"] != 0)]
    df = df.reset_index(drop=True)

    return df


def normaliser_df_gl(df: pd.DataFrame) -> pd.DataFrame:
    """
    Version spécialisée pour le Grand Livre.
    Détecte automatiquement si le fichier a des N° compte ou des libellés seulement.
    Si le df vient du parseur FEC (colonnes déjà normalisées), skip normaliser_df.
    """
    # Si les colonnes core sont déjà présentes et numériques → FEC déjà parsé
    has_core = all(c in df.columns for c in ["compte", "libelle", "debit", "credit"])
    if has_core and pd.api.types.is_numeric_dtype(df["debit"]):
        df = df.copy()
        df["compte"] = df["compte"].astype(str).str.strip()
        df["libelle"] = df["libelle"].astype(str).fillna("")
        df["debit"]   = df["debit"].fillna(0.0)
        df["credit"]  = df["credit"].fillna(0.0)
        df = df[(df["debit"] != 0) | (df["credit"] != 0)].reset_index(drop=True)
    else:
        df = normaliser_df(df)

    # Vérifier si les comptes sont bien des numéros PCGE
    if len(df) == 0:
        return df

    pct_pcge = df["compte"].str.match(r"^[1-9]\d{2,}").mean()

    if pct_pcge < 0.3:
        # Comptes non reconnus → mapper depuis libellé
        if "libelle" in df.columns:
            df["compte"] = df["libelle"].apply(mapper_compte_depuis_libelle)
        # Les ventes en débit = convention tiers (3421 → 7111)
        # Les achats en débit = convention charges
        # Détecter et corriger si nécessaire le sens
        pass

    # Pour comptes 7xxx (produits) : normaliser vers crédit
    # Si les ventes sont saisies au débit (convention certains logiciels), inverser
    mask_7 = df["compte"].str.match(r"^7")
    if mask_7.any():
        ca_debit  = df[mask_7]["debit"].sum()
        ca_credit = df[mask_7]["credit"].sum()
        if ca_debit > ca_credit * 2 and ca_debit > 0:
            # Les ventes sont saisies au débit → inverser pour analyse
            df.loc[mask_7, ["debit", "credit"]] = df.loc[mask_7, ["credit", "debit"]].values

    return df
