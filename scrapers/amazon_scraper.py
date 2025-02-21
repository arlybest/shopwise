#!/usr/bin/env python3
import logging
import re
import time
import random
import requests
from bs4 import BeautifulSoup
import pandas as pd
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, jsonify, request
from flask_cors import CORS

# -----------------------------------------------------------------------------
# Configuration du Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    filename="amazon_scraper.log", 
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logging.getLogger().addHandler(console_handler)

# -----------------------------------------------------------------------------
# Création de l'application Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)  # Autorise toutes les origines

# -----------------------------------------------------------------------------
# Fonctions Utilitaires
# -----------------------------------------------------------------------------
def get_url(search_term, page=1):
    """
    Génère l'URL de recherche Amazon pour le mot-clé donné et la page spécifiée.
    """
    base = "https://www.amazon.com/s"
    search_term = search_term.replace(" ", "+")
    return f"{base}?k={search_term}&page={page}"

def convert_price_to_fcfa(price_str):
    """
    Convertit un montant exprimé en devise (€, $, £) en FCFA.
    Facteurs de conversion approximatifs :
      - Dollar ($) : 600 FCFA par USD
      - Euro (€)   : 655.957 FCFA par EUR
      - Livre sterling (£) : 800 FCFA par GBP
    """
    if not price_str or price_str == "N/A":
        return "N/A"
    
    price_str = price_str.strip()
    conversion_factor = None
    if "$" in price_str:
        conversion_factor = 600.0
        price_numeric_str = price_str.replace("$", "").strip()
    elif "€" in price_str:
        conversion_factor = 655.957
        price_numeric_str = price_str.replace("€", "").strip()
    elif "£" in price_str:
        conversion_factor = 800.0
        price_numeric_str = price_str.replace("£", "").strip()
    else:
        conversion_factor = 600.0
        price_numeric_str = price_str

    # Conserver uniquement chiffres, virgule et point
    price_numeric_str = re.sub(r"[^\d,\.]", "", price_numeric_str)
    if price_numeric_str.count(",") == 1 and "." not in price_numeric_str:
        price_numeric_str = price_numeric_str.replace(",", ".")
    else:
        price_numeric_str = price_numeric_str.replace(",", "")
    
    try:
        price_value = float(price_numeric_str)
        converted_value = price_value * conversion_factor
        return f"{converted_value:,.2f} FCFA"
    except Exception as e:
        logging.error(f"Erreur lors de la conversion du prix '{price_str}': {e}")
        return price_str

def scrape_records(item):
    """
    Extrait les informations d'un produit à partir d'un élément HTML.
    Récupère :
      - La description et l'URL du produit,
      - Le prix (converti en FCFA),
      - Le rating,
      - L'URL de l'image,
      - Les frais cachés (exemple : frais de livraison convertis en FCFA),
      - La source (Amazon).
    """
    try:
        # Description et URL
        description = "N/A"
        product_url = "N/A"
        title_container = item.select_one("div[data-cy='title-recipe']")
        if title_container:
            h2 = title_container.select_one("h2")
            if h2:
                description = h2.get_text(strip=True)
            else:
                a_tag = title_container.find("a")
                if a_tag:
                    description = a_tag.get_text(strip=True)
            a_tag = title_container.find("a")
            if a_tag and a_tag.has_attr("href"):
                product_url = "https://amazon.com" + a_tag["href"]
        # Fallback en cas d'absence du container principal
        if product_url == "N/A":
            a_tag = item.find("a", href=re.compile("/dp/"))
            if a_tag and a_tag.has_attr("href"):
                product_url = "https://amazon.com" + a_tag["href"]
            if description == "N/A":
                alt_title = item.select_one("h2.a-size-base-plus")
                if alt_title:
                    description = alt_title.get_text(strip=True)
        
        # Prix principal
        price_container = item.select_one("div[data-cy='price-recipe'] span.a-offscreen")
        price = price_container.get_text(strip=True) if price_container else "N/A"
        price_fcfa = convert_price_to_fcfa(price)

        # Rating
        rating_container = item.select_one("span.a-icon-alt")
        rating = rating_container.get_text(strip=True) if rating_container else "No Rating"

        # Image URL
        image_container = item.find("img", class_="s-image")
        image_url = image_container.get("src") if image_container else "N/A"

        # Extraction des frais cachés (exemple : frais de livraison)
        hidden_fees_container = item.select_one("div[data-cy='delivery-recipe'] span.a-color-base")
        if hidden_fees_container:
            hidden_fees_text = hidden_fees_container.get_text(strip=True)
            # Suppression du préfixe "Livraison à" s'il existe
            hidden_fees_text = hidden_fees_text.replace("Livraison à", "").strip()
            hidden_fees_fcfa = convert_price_to_fcfa(hidden_fees_text)
        else:
            hidden_fees_fcfa = "N/A"

        logging.info(f"✔ Produit extrait : {description} - {price} -> {price_fcfa} | Frais cachés : {hidden_fees_fcfa}")
        return {
            "description": description,
            "price": price_fcfa,
            "rating": rating,
            "productURL": product_url,
            "imageURL": image_url,
            "hiddenFees": hidden_fees_fcfa,
            "sourceLogo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Amazon_logo.svg/1024px-Amazon_logo.svg.png",
            "source": "Amazon"
        }
    
    except Exception as e:
        logging.error(f"❌ Erreur lors de l'extraction d'un produit : {e}")
        return None

def fetch_page(session, search_term, page, headers):
    """
    Récupère et parse une page donnée pour un terme de recherche.
    Implémente une stratégie de retry en cas d'échec.
    """
    url = get_url(search_term, page)
    logging.info(f"🌐 Récupération de la page {page} : {url}")
    for attempt in range(3):  # 3 tentatives maximum
        logging.info(f"🔄 Tentative {attempt+1} pour la page {page}")
        try:
            response = session.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return page, BeautifulSoup(response.content, "html.parser")
            else:
                logging.error(f"❌ Erreur HTTP {response.status_code} pour la page {page}")
        except Exception as e:
            logging.error(f"❌ Exception lors de la récupération de la page {page} : {e}")
        time.sleep(1)  # Pause avant de réessayer
    return page, None

def scrape_amazon(search_term):
    """
    Scrape les résultats Amazon pour le terme de recherche donné sur 5 pages en parallèle.
    """
    logging.info(f"🔍 Démarrage du scraping pour : {search_term}")
    
    ua = UserAgent()
    headers = {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    session = requests.Session()
    records = []
    pages_to_fetch = list(range(1, 6))  # Pages 1 à 5

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_page = {
            executor.submit(fetch_page, session, search_term, page, headers): page
            for page in pages_to_fetch
        }
        for future in as_completed(future_to_page):
            page, soup = future.result()
            if not soup:
                logging.warning(f"⚠ Aucune donnée récupérée pour la page {page}.")
                continue
            logging.info(f"📄 Scraping de la page {page}...")
            results = soup.find_all("div", {"data-component-type": "s-search-result"})
            if not results:
                logging.warning(f"⚠ Aucune donnée trouvée sur la page {page}.")
                continue
            for item in results:
                record = scrape_records(item)
                if record:
                    records.append(record)
            time.sleep(0.2)  # Pause très courte pour limiter la charge
    
    df = pd.DataFrame(records, columns=["description", "price", "rating", "productURL", "imageURL", "hiddenFees", "source", "sourceLogo"])
    
    # Tri des résultats par prix croissant
    def parse_price(price_str):
        try:
            return float(price_str.replace(" FCFA", "").replace(",", ""))
        except Exception:
            return 0.0

    if not df.empty:
        df["price_numeric"] = df["price"].apply(parse_price)
        df = df.sort_values(by="price_numeric", ascending=True).drop(columns=["price_numeric"])
    
    return df

# -----------------------------------------------------------------------------
# Définition de l'Endpoint Flask
# -----------------------------------------------------------------------------
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    df = scrape_amazon(query)
    if df is not None and not df.empty:
        records = df.to_dict(orient="records")
        return jsonify(records)
    else:
        return jsonify({"error": "Aucune donnée extraite."}), 404

# -----------------------------------------------------------------------------
# Code de Test Initial et Démarrage de l'Application
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Partie test : Récupération d'une page pour vérifier la configuration des headers
    ua = UserAgent()
    test_headers = {
        "User-Agent": ua.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }
    test_url = "https://www.amazon.com/s?k=macbook&page=1"
    try:
        response = requests.get(test_url, headers=test_headers, timeout=10)
        if response.status_code == 200:
            logging.info("✅ Succès ! Contenu récupéré pour le test initial.")
        else:
            logging.error(f"❌ Erreur {response.status_code} - Impossible d'accéder à la page pour le test initial.")
    except Exception as e:
        logging.error(f"❌ Exception lors du test initial : {e}")

    # Démarrage du serveur Flask sur le port 5000
    app.run(debug=True, host='0.0.0.0', port=5000)
