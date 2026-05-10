# FiscalAudit Pro v2 — Maroc

## Lancement
```bash
pip install -r requirements.txt
python app.py  # → http://localhost:5050
```

## Fichiers modifiés/ajoutés vs v1

| Fichier | Statut | Description |
|---|---|---|
| `engines/parseurs.py` | **NOUVEAU** | Parser FEC natif + balance auto + relevés multi-banques + TVA + 9421 + liasse |
| `engines/blocs456.py` | **MODIFIÉ** | Correction IE-001 (CA N/N-1), correction D9421 (6123 seulement), CGNC-003 (exclure 619x), Bloc 7 |
| `engines/bloc1_tva.py` | **MODIFIÉ** | Ajout `lignes_sources` sur chaque anomalie |
| `engines/rapport_pdf.py` | **MODIFIÉ** | Nouvelle page "Lignes sources" avec écritures FEC |
| `app.py` | **MODIFIÉ** | FEC natif, balance calculée auto, TVA fichier/manuel, Bloc 7 |
| `templates/index.html` | **MODIFIÉ** | Upload FEC N+N-1, options TVA fichier/manuel, consignes nomenclature 6 banques |
| `engines/normalisation.py` | Inchangé | |
| `engines/bloc2_is.py` | Inchangé | |
| `engines/bloc3_banque.py` | Inchangé | |

## Format FEC accepté
Exporté depuis : Sage 50/100/100cloud/X3, Ciel, EBP, KHABIR
Menu Sage : `Fichier → Exporter → Fichier des Écritures Comptables → OK`

## Relevés bancaires supportés
CIH Bank, Attijariwafa, BMCE/Bank of Africa, Banque Populaire, BMCI, Société Générale Maroc
