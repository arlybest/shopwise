import logging
import requests
from bs4 import BeautifulSoup
import re
import time
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS

# Configuration du logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Création de l'application Flask
app = Flask(__name__)
CORS(app)

BASE_URL = "https://glotelho.cm/search?q={query}&limit=40&page={page}"

def fetch_page(search_term, page):
    """Récupère et parse une page donnée pour un terme de recherche."""
    url = BASE_URL.format(query=search_term, page=page)
    logging.info(f"🌐 Récupération de la page {page} : {url}")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return BeautifulSoup(response.content, "html.parser")
        else:
            logging.error(f"❌ Erreur HTTP {response.status_code} pour {url}")
            return None
    except Exception as e:
        logging.error(f"❌ Exception lors de la récupération de {url} : {e}")
        return None

def scrape_glotelho(search_term, max_pages=3):
    """Scrape les produits depuis Glotelho en évitant les doublons."""
    records = []
    seen = set()  # Ensemble pour stocker les clés uniques (URL ou description)
    for page in range(1, max_pages + 1):
        soup = fetch_page(search_term, page)
        if not soup:
            continue

        product_containers = soup.find_all("div", class_=re.compile("flex flex-col justify-between"))
        for product in product_containers:
            try:
                link_tag = product.find("a", href=True)
                title = link_tag.find("h3").text.strip() if link_tag else "N/A"
                product_url = "https://glotelho.cm" + link_tag["href"] if link_tag else "N/A"

                # Extraction correcte de l'image : on prend data-src si présent, sinon src
                image_tag = product.find("img")
                image_url = image_tag["data-src"] if image_tag and "data-src" in image_tag.attrs else (image_tag["src"] if image_tag else "N/A")

                price_tag = product.find("span", class_=re.compile("font-bold text-gray-900"))
                price = price_tag.text.strip().replace("\u00a0", " ") if price_tag else "N/A"
                
                old_price_tag = product.find("span", class_=re.compile("line-through"))
                old_price = old_price_tag.text.strip().replace("\u00a0", " ") if old_price_tag else "N/A"

                # Définir une clé unique pour éviter les doublons (URL si disponible, sinon description)
                key = product_url if product_url != "N/A" else title

                if key not in seen:
                    seen.add(key)
                    records.append({
                        "description": title,
                        "price": price,
                        "old_price": old_price,
                        "productURL": product_url,
                        "imageURL": image_url,
                        "source": "https://glotelho.cm/images/glotelho-ecommerce.jpg"
                    })
            except Exception as e:
                logging.error(f"Erreur d'extraction : {e}")
        time.sleep(1)
    
    # Trier les résultats par prix du moins cher au plus cher
    def extract_price(price_str):
        return float(re.sub(r"[^0-9]", "", price_str)) if re.search(r"\d", price_str) else float('inf')
    
    records = sorted(records, key=lambda x: extract_price(x["price"]))
    return records

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    results = scrape_glotelho(query)
    return jsonify(results if results else {"error": "Aucune donnée extraite."})

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
