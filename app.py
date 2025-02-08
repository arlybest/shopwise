from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

# Importation des fonctions de scraping depuis vos modules
from scrapers.amazon_scraper import scrape_amazon
from scrapers.glotehlo_scraper import scrape_glotelho

app = Flask(__name__)
CORS(app)  # Autorise les requêtes CORS

def extract_price(price_str):
    """
    Extrait la valeur numérique d'une chaîne de prix.
    Gère les formats avec des espaces (ex: "72 000 FCFA") et des virgules (ex: "8,994.00 FCFA").
    En cas d'échec, retourne float('inf').
    """
    try:
        # Supprime " FCFA" et élimine les séparateurs d'espaces et de virgules.
        # Pour Amazon : "8,994.00 FCFA" -> "8994.00"
        # Pour Glotehlo : "72 000 FCFA"   -> "72000"
        cleaned = price_str.replace(" FCFA", "").replace(",", "").replace(" ", "").strip()
        return float(cleaned)
    except Exception:
        return float('inf')

def format_price(price_value):
    """
    Formate une valeur numérique en chaîne avec séparateur de milliers et deux décimales.
    Par exemple, 8994.0 devient "8,994.00 FCFA".
    """
    return "{:,.2f} FCFA".format(price_value)

def compute_deal_attributes(record):
    """
    Calcule et ajoute dans le record l'attribut numeric_price (prix converti en nombre)
    et formate le champ "price" de manière cohérente.
    """
    numeric_price = extract_price(record.get("price", ""))
    record["numeric_price"] = numeric_price
    # Remplace le prix d'origine par le prix formaté
    record["price"] = format_price(numeric_price)
    return record

@app.route('/search', methods=['GET'])
def search():
    """
    Endpoint qui reçoit un paramètre 'query', lance en parallèle les scrapers sur Amazon et Glotehlo,
    combine les résultats, filtre les offres dont le prix est invalide, calcule le prix numérique,
    formate les prix de manière cohérente, puis trie les offres par prix croissant (les moins chers en premier).
    """
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    
    try:
        # Exécuter les deux scrapers en parallèle
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_amazon = executor.submit(scrape_amazon, query)
            future_glotehlo = executor.submit(scrape_glotelho, query)
            amazon_df = future_amazon.result()
            glotehlo_records = future_glotehlo.result()
            
        # Conversion du DataFrame Amazon en liste de dictionnaires
        if amazon_df is not None and not amazon_df.empty:
            amazon_records = amazon_df.to_dict(orient="records")
        else:
            amazon_records = []
        
        # Combinaison des résultats des deux sites
        combined_results = amazon_records + glotehlo_records
        
        # Filtrer les offres dont le prix est manquant ou invalide et calculer les attributs
        filtered_results = []
        for record in combined_results:
            if "price" not in record or not record["price"] or record["price"] == "N/A":
                continue
            numeric_price = extract_price(record["price"])
            if numeric_price == float('inf'):
                continue
            record = compute_deal_attributes(record)
            filtered_results.append(record)
        
        # Tri par prix croissant (les offres les moins chères en premier)
        sorted_results = sorted(filtered_results, key=lambda r: r["numeric_price"])
        
        # Optionnel : retirer l'attribut interne numeric_price pour ne pas l'exposer dans l'API
        for record in sorted_results:
            record.pop("numeric_price", None)
        
        return jsonify(sorted_results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Lancer l'application Flask en autorisant l'accès depuis le réseau local
    app.run(debug=True, host="0.0.0.0", port=5000)
