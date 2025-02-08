import logging
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from fake_useragent import UserAgent
import re
from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration du logging
logging.basicConfig(
    filename="walmart_scraper.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logging.getLogger().addHandler(console_handler)

# Création de l'application Flask
app = Flask(__name__)
CORS(app)

def get_url(search_term, page=1):
    """
    Génère l'URL de recherche Walmart pour le terme donné et la page spécifiée.
    Exemple : https://www.walmart.com/search?q=vaisselle&page=1
    """
    base = "https://www.walmart.com/search"
    search_term = search_term.replace(" ", "+")
    return f"{base}?q={search_term}&page={page}"

def convert_price_to_fcfa(price_str):
    """
    Convertit un prix en dollars (exemple "$5.51") en FCFA.
    Taux utilisé : 600 FCFA par USD.
    """
    if not price_str or price_str == "N/A":
        return "N/A"
    
    price_str = price_str.strip()
    # Supprime les caractères non numériques (sauf virgule et point)
    price_numeric_str = re.sub(r"[^\d,\.]", "", price_str)
    # S'il y a une seule virgule et pas de point, c'est le séparateur décimal
    if price_numeric_str.count(",") == 1 and "." not in price_numeric_str:
        price_numeric_str = price_numeric_str.replace(",", ".")
    else:
        price_numeric_str = price_numeric_str.replace(",", "")
    
    try:
        price_value = float(price_numeric_str)
        converted_value = price_value * 600.0
        return f"{converted_value:,.2f} FCFA"
    except Exception as e:
        logging.error(f"Erreur lors de la conversion du prix '{price_str}': {e}")
        return price_str

def scrape_walmart_record(item):
    """
    Extrait les informations d'un produit à partir d'un élément HTML.
    Pour chaque produit, on tente de récupérer :
      - La description (prioritairement via <span data-automation-id="product-title">)
      - L'URL du produit (recherche d'un <a> dont le href contient "/ip/")
      - Le prix (puis converti en FCFA)
      - Le rating (via data-testid="product-ratings" ou par recherche dans le texte)
      - L'URL de l'image (depuis <img data-testid="productTileImage">)
    """
    try:
        description = "N/A"
        product_url = "N/A"
        
        # Essai de récupérer le titre/description
        title_span = item.find("span", {"data-automation-id": "product-title"})
        if title_span:
            description = title_span.get_text(strip=True)
        
        # Récupération de l'URL du produit
        a_tag = item.find("a", href=re.compile(r"/ip/"))
        if a_tag and a_tag.has_attr("href"):
            product_url = "https://www.walmart.com" + a_tag["href"]
            if description == "N/A":
                description = a_tag.get_text(strip=True)
        else:
            a_tag = item.find("a", href=True)
            if a_tag:
                product_url = "https://www.walmart.com" + a_tag["href"]
                if description == "N/A":
                    description = a_tag.get_text(strip=True)
        
        # Extraction du prix (on prend la première occurrence au format "$xx.xx")
        price = "N/A"
        price_div = item.find("div", {"data-automation-id": "product-price"})
        if price_div:
            text = price_div.get_text(separator=" ", strip=True)
            matches = re.findall(r"\$(\d+\.\d{2})", text)
            if matches:
                price = "$" + matches[0]
        price_fcfa = convert_price_to_fcfa(price)
        
        # Extraction du rating
        rating = "No Rating"
        rating_span = item.find("span", {"data-testid": "product-ratings"})
        if rating_span and rating_span.has_attr("data-value"):
            rating = rating_span["data-value"]
        else:
            # Recherche dans le texte une mention du type "X out of 5"
            rating_search = re.search(r"(\d+\.\d+)\s*out of\s*5", item.get_text(), re.IGNORECASE)
            if rating_search:
                rating = rating_search.group(1)
        
        # Extraction de l'image
        image_url = "N/A"
        image_tag = item.find("img", {"data-testid": "productTileImage"})
        if image_tag and image_tag.has_attr("src"):
            image_url = image_tag["src"]
        
        logging.info(f"Produit extrait: {description} - {price} -> {price_fcfa} - Rating: {rating}")
        return {
            "description": description,
            "price": price_fcfa,
            "rating": rating,
            "productURL": product_url,
            "imageURL": image_url,
            "source": "Walmart",
            "sourceLogo": "https://1000logos.net/wp-content/uploads/2017/05/Walmart-Logo.png"
        }
    
    except Exception as e:
        logging.error(f"Erreur lors de l'extraction d'un produit: {e}")
        return None

def fetch_page(session, search_term, page, headers):
    """
    Récupère et parse une page donnée pour le terme de recherche.
    Retourne un tuple (page, BeautifulSoup).
    """
    url = get_url(search_term, page)
    logging.info(f"🌐 Récupération de la page {page}: {url}")
    try:
        response = session.get(url, headers=headers)
        if response.status_code == 200:
            return page, BeautifulSoup(response.content, "html.parser")
        else:
            logging.error(f"❌ Erreur lors de la récupération de la page {page}, code HTTP: {response.status_code}")
            return page, None
    except Exception as e:
        logging.error(f"❌ Exception lors de la récupération de la page {page}: {e}")
        return page, None

def scrape_walmart(search_term):
    """
    Scrape les résultats Walmart pour le terme de recherche donné sur 5 pages fixes.
    Retourne un DataFrame trié par prix croissant.
    """
    logging.info(f"🔍 Début du scraping Walmart pour: {search_term}")
    
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
    # Forçage à 5 pages, même si le site en affiche plus
    pages_to_fetch = list(range(1, 6))
    
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
            # On récupère tous les produits identifiés par un attribut data-item-id
            product_items = soup.find_all("div", {"data-item-id": True})
            for item in product_items:
                record = scrape_walmart_record(item)
                if record:
                    records.append(record)
            time.sleep(0.2)
    
    df = pd.DataFrame(records, columns=["description", "price", "rating", "productURL", "imageURL", "source", "sourceLogo"])
    
    # Déduplication : suppression des doublons basés sur l'URL du produit
    if not df.empty:
        df = df.drop_duplicates(subset=["productURL"])
    
    # Tri des résultats par prix croissant (en extrayant la valeur numérique du prix)
    def parse_price(price_str):
        try:
            return float(price_str.replace(" FCFA", "").replace(",", ""))
        except Exception:
            return float('inf')
    
    if not df.empty:
        df["price_numeric"] = df["price"].apply(parse_price)
        df = df.sort_values(by="price_numeric", ascending=True).drop(columns=["price_numeric"])
    
    return df

# Définition de l'endpoint Flask
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    df = scrape_walmart(query)
    if df is not None and not df.empty:
        records = df.to_dict(orient="records")
        return jsonify(records)
    else:
        return jsonify({"error": "Aucune donnée extraite."}), 404

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
