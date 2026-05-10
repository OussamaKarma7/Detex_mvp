"""
FiscalAudit Pro v2 — Application Flask
Support FEC natif, relevés multi-banques, TVA fichier/manuel, liasse fiscale
"""
import os, uuid, json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd

from engines.parseurs import (
    lire_fec, lire_releve_bancaire, lire_declarations_tva,
    lire_declaration_9421, lire_liasse_fiscale,
    calculer_balance_depuis_fec, ParseResult
)
from engines.bloc1_tva import analyser_tva
from engines.bloc2_is import analyser_is
from engines.bloc3_banque import analyser_rapprochement_bancaire
from engines.blocs456 import (
    analyser_inter_exercices, analyser_conformite_cgnc,
    analyser_declaration_9421, analyser_liasse_fiscale
)
from engines.rapport_pdf import generer_rapport_pdf

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["REPORTS_FOLDER"] = os.path.join(os.path.dirname(__file__), "reports")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB

ALLOWED = {"xlsx", "xls", "csv", "txt"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED


def sauver(file, prefix):
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{file.filename.rsplit('.',1)[-1]}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)
    return path


def lire_fichier_brut(filepath: str) -> pd.DataFrame:
    """Lit Excel ou CSV avec détection automatique de header."""
    from engines.normalisation import detecter_header_row
    ext = filepath.rsplit(".", 1)[-1].lower()
    encodings = ["utf-8", "latin-1", "cp1252", "utf-8-sig"]
    try:
        if ext in ("xlsx", "xls"):
            df_raw = pd.read_excel(filepath, header=None, dtype=str)
            hr = detecter_header_row(df_raw)
            df = pd.read_excel(filepath, header=hr, dtype=str)
        else:
            df_raw = enc_used = None
            for enc in encodings:
                try:
                    df_raw = pd.read_csv(filepath, header=None, encoding=enc,
                                         sep=None, engine="python", dtype=str)
                    enc_used = enc
                    break
                except Exception:
                    continue
            if df_raw is None:
                return pd.DataFrame()
            hr = detecter_header_row(df_raw)
            df = pd.read_csv(filepath, header=hr, encoding=enc_used,
                             sep=None, engine="python", dtype=str)
        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
        df = df.dropna(how="all").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def est_fec(filepath: str) -> bool:
    """Détecte si un fichier est au format FEC (contient CompteNum + Debit/Credit)."""
    try:
        ext = filepath.rsplit(".", 1)[-1].lower()
        for enc in ["utf-8", "latin-1", "cp1252"]:
            try:
                with open(filepath, "r", encoding=enc, errors="replace") as f:
                    header = f.readline().lower()
                if "comptenum" in header and ("debit" in header or "crédit" in header):
                    return True
                break
            except Exception:
                continue
        return False
    except Exception:
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyser", methods=["POST"])
def analyser():
    try:
        blocs_actifs = request.form.getlist("blocs")
        nom_entreprise = request.form.get("nom_entreprise", "Entreprise")
        exercice = int(request.form.get("exercice", datetime.now().year - 1))
        ca_exercice = float(request.form.get("ca_exercice", 0) or 0)
        resultat_comptable = float(request.form.get("resultat_comptable", 0) or 0)

        if not blocs_actifs:
            return jsonify({"error": "Sélectionnez au moins un bloc"}), 400

        # ── Sauvegarder les fichiers ──
        fichiers_paths = {}
        for cle in ["fec_n", "fec_n1", "releve_bancaire", "declarations_tva",
                    "declaration_9421", "liasse_fiscale"]:
            if cle in request.files and request.files[cle].filename:
                f = request.files[cle]
                if allowed_file(f.filename):
                    fichiers_paths[cle] = sauver(f, cle)

        # ── Parser les fichiers ──
        warnings_global = []

        # FEC N
        fec_n_result = None
        fec_n_df = pd.DataFrame()
        balance_n_df = pd.DataFrame()
        if "fec_n" in fichiers_paths:
            fec_n_result = lire_fec(fichiers_paths["fec_n"])
            if fec_n_result.erreur:
                return jsonify({"error": f"FEC N : {fec_n_result.erreur}"}), 400
            fec_n_df = fec_n_result.df
            balance_n_df = calculer_balance_depuis_fec(fec_n_df)
            warnings_global.extend(fec_n_result.warnings)

        # FEC N-1
        fec_n1_df = pd.DataFrame()
        balance_n1_df = pd.DataFrame()
        if "fec_n1" in fichiers_paths:
            fec_n1_result = lire_fec(fichiers_paths["fec_n1"])
            if not fec_n1_result.erreur:
                fec_n1_df = fec_n1_result.df
                balance_n1_df = calculer_balance_depuis_fec(fec_n1_df)
                warnings_global.extend(fec_n1_result.warnings)

        # Relevé bancaire
        releve_df = pd.DataFrame()
        if "releve_bancaire" in fichiers_paths:
            rb_result = lire_releve_bancaire(fichiers_paths["releve_bancaire"])
            releve_df = rb_result.df
            warnings_global.extend(rb_result.warnings)

        # Déclarations TVA — fichier ou saisie manuelle
        tva_df = pd.DataFrame()
        if "declarations_tva" in fichiers_paths:
            tva_result = lire_declarations_tva(fichiers_paths["declarations_tva"])
            tva_df = tva_result.df
            warnings_global.extend(tva_result.warnings)
        else:
            tva_json = request.form.get("declarations_tva_manuel", "[]")
            try:
                tva_data = json.loads(tva_json)
                tva_df = pd.DataFrame(tva_data) if tva_data else pd.DataFrame()
            except Exception:
                tva_df = pd.DataFrame()

        # Déclaration 9421
        d9421_df = pd.DataFrame()
        if "declaration_9421" in fichiers_paths:
            d9421_result = lire_declaration_9421(fichiers_paths["declaration_9421"])
            d9421_df = d9421_result.df
            warnings_global.extend(d9421_result.warnings)

        # Liasse fiscale (Bilan/CPC/ESG)
        liasse = {}
        if "liasse_fiscale" in fichiers_paths:
            liasse, w = lire_liasse_fiscale(fichiers_paths["liasse_fiscale"])
            warnings_global.extend(w)

        # ── Calcul CA depuis FEC si non fourni ──
        if not ca_exercice and not fec_n_df.empty:
            mask_7 = fec_n_df["compte"].str.match(r"^7")
            ca_exercice = fec_n_df[mask_7]["credit"].sum() - fec_n_df[mask_7]["debit"].sum()

        # ── Lancer les moteurs ──
        toutes_anomalies = []
        blocs_analyses = []

        if "bloc1" in blocs_actifs:
            if fec_n_df.empty:
                return jsonify({"error": "Bloc 1 nécessite le FEC N"}), 400
            blocs_analyses.append("BLOC 1 - TVA")
            toutes_anomalies.extend(analyser_tva(fec_n_df, tva_df, exercice))

        if "bloc2" in blocs_actifs:
            if fec_n_df.empty:
                return jsonify({"error": "Bloc 2 nécessite le FEC N"}), 400
            blocs_analyses.append("BLOC 2 - IS")
            toutes_anomalies.extend(analyser_is(
                fec_n_df, balance_n_df,
                ca_exercice or None, resultat_comptable or None, exercice
            ))

        if "bloc3" in blocs_actifs:
            if fec_n_df.empty or releve_df.empty:
                return jsonify({"error": "Bloc 3 nécessite FEC N + Relevé bancaire"}), 400
            blocs_analyses.append("BLOC 3 - Banque")
            toutes_anomalies.extend(analyser_rapprochement_bancaire(fec_n_df, releve_df))

        if "bloc4" in blocs_actifs:
            if balance_n_df.empty or balance_n1_df.empty:
                return jsonify({"error": "Bloc 4 nécessite FEC N + FEC N-1"}), 400
            blocs_analyses.append("BLOC 4 - Inter-Exercices")
            toutes_anomalies.extend(analyser_inter_exercices(balance_n_df, balance_n1_df))

        if "bloc5" in blocs_actifs:
            if fec_n_df.empty:
                return jsonify({"error": "Bloc 5 nécessite le FEC N"}), 400
            blocs_analyses.append("BLOC 5 - CGNC")
            toutes_anomalies.extend(analyser_conformite_cgnc(fec_n_df))

        if "bloc6" in blocs_actifs:
            if fec_n_df.empty:
                return jsonify({"error": "Bloc 6 nécessite le FEC N"}), 400
            blocs_analyses.append("BLOC 6 - Déclaration 9421")
            toutes_anomalies.extend(analyser_declaration_9421(fec_n_df, d9421_df))

        if "bloc7" in blocs_actifs:
            if not liasse:
                return jsonify({"error": "Bloc 7 nécessite le fichier de liasse fiscale (Bilan/CPC)"}), 400
            blocs_analyses.append("BLOC 7 - Liasse Fiscale")
            toutes_anomalies.extend(analyser_liasse_fiscale(liasse, fec_n_df, ca_exercice))

        # ── Rapport PDF ──
        rapport_id = uuid.uuid4().hex[:12]
        chemin_pdf = os.path.join(app.config["REPORTS_FOLDER"], f"audit_{rapport_id}.pdf")
        generer_rapport_pdf(toutes_anomalies, nom_entreprise, exercice, blocs_analyses, chemin_pdf)

        score = min(sum(15 if a.severity=="CRITIQUE" else 7 if a.severity=="MOYEN" else 2
                        for a in toutes_anomalies), 100)

        return jsonify({
            "success": True,
            "rapport_id": rapport_id,
            "score_risque": score,
            "total_anomalies": len(toutes_anomalies),
            "critiques": sum(1 for a in toutes_anomalies if a.severity=="CRITIQUE"),
            "moyens": sum(1 for a in toutes_anomalies if a.severity=="MOYEN"),
            "faibles": sum(1 for a in toutes_anomalies if a.severity=="FAIBLE"),
            "warnings": warnings_global,
            "ca_detecte": round(ca_exercice, 0),
            "anomalies": [{
                "bloc": a.bloc, "code": a.code, "severity": a.severity,
                "compte": a.compte, "montant": a.montant,
                "description": a.description, "reference_cgi": a.reference_cgi,
                "recommandation": a.recommandation, "detail": a.detail,
            } for a in toutes_anomalies],
            "blocs_analyses": blocs_analyses,
        })

    except Exception as e:
        import traceback
        return jsonify({"error": f"Erreur : {str(e)}", "trace": traceback.format_exc()}), 500


@app.route("/api/rapport/<rapport_id>")
def telecharger_rapport(rapport_id):
    chemin = os.path.join(app.config["REPORTS_FOLDER"], f"audit_{rapport_id}.pdf")
    if not os.path.exists(chemin):
        return jsonify({"error": "Rapport non trouvé"}), 404
    return send_file(chemin, as_attachment=True,
                     download_name=f"audit_fiscal_{rapport_id}.pdf",
                     mimetype="application/pdf")


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["REPORTS_FOLDER"], exist_ok=True)
    app.run(debug=True, port=5050)
