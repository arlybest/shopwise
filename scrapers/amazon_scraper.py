import logging
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from fake_useragent import UserAgent
import re  # Pour la recherche d'URL via une expression régulière
from flask import Flask, jsonify, request
from flask_cors import CORS  # Pour autoriser les requêtes CORS
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration du logging
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

# Création de l'application Flask
app = Flask(__name__)
CORS(app)  # Autorise toutes les origines (vous pouvez restreindre en passant des arguments)

def get_url(search_term, page=1):
    """Génère l'URL de recherche Amazon pour le mot-clé donné et la page spécifiée."""
    base = "https://www.amazon.com/s"
    search_term = search_term.replace(" ", "+")
    return f"{base}?k={search_term}&page={page}"

def convert_price_to_fcfa(price_str):
    """
    Détecte l'unité de prix et convertit le montant en FCFA.
    
    Facteurs de conversion (valeurs approximatives) :
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

    # Supprimer tout ce qui n'est pas chiffre, virgule ou point
    price_numeric_str = re.sub(r"[^\d,\.]", "", price_numeric_str)
    # S'il y a une seule virgule et pas de point, elle est probablement décimale
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
    Extrait les informations d'un produit en tenant compte de la nouvelle structure HTML.
    On récupère :
      - la description et l'URL du produit depuis le conteneur data-cy="title-recipe" (avec fallback),
      - le prix depuis le conteneur data-cy="price-recipe" (converti en FCFA),
      - le rating depuis le span "a-icon-alt",
      - l'URL de l'image depuis l'élément <img class="s-image">,
      - et la source "Amazon".
    """
    try:
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
        if product_url == "N/A":
            a_tag = item.find("a", href=re.compile("/dp/"))
            if a_tag and a_tag.has_attr("href"):
                product_url = "https://amazon.com" + a_tag["href"]
            if description == "N/A":
                alt_title = item.select_one("h2.a-size-base-plus")
                if alt_title:
                    description = alt_title.get_text(strip=True)
        
        price_container = item.select_one("div[data-cy='price-recipe'] span.a-offscreen")
        price = price_container.get_text(strip=True) if price_container else "N/A"
        price_fcfa = convert_price_to_fcfa(price)

        rating_container = item.select_one("span.a-icon-alt")
        rating = rating_container.get_text(strip=True) if rating_container else "No Rating"

        image_container = item.find("img", class_="s-image")
        image_url = image_container.get("src") if image_container else "N/A"

        logging.info(f"✔ Produit extrait : {description} - {price} -> {price_fcfa}")
        return {
            "description": description,
            "price": price_fcfa,
            "rating": rating,
            "productURL": product_url,
            "imageURL": image_url,
            "sourceLogo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Amazon_logo.svg/1024px-Amazon_logo.svg.png",
            "source":"Amazon"
        }
    
    except Exception as e:
        logging.error(f"❌ Erreur lors de l'extraction d'un produit : {e}")
        return None

def fetch_page(session, search_term, page, headers):
    """Récupère et parse une page donnée pour un terme de recherche."""
    url = get_url(search_term, page)
    logging.info(f"🌐 Récupération de la page {page} : {url}")
    try:
        response = session.get(url, headers=headers)
        if response.status_code == 200:
            return page, BeautifulSoup(response.content, "html.parser")
        else:
            logging.error(f"❌ Erreur lors de la récupération de la page {page}, code HTTP : {response.status_code}")
            return page, None
    except Exception as e:
        logging.error(f"❌ Exception lors de la récupération de la page {page} : {e}")
        return page, None

def scrape_amazon(search_term):
    """Scrape les résultats Amazon pour le terme de recherche donné pour 5 pages en parallèle."""
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
            # Pause très courte pour limiter la charge (réduite pour plus de rapidité)
            time.sleep(0.2)
    
    df = pd.DataFrame(records, columns=["description", "price", "rating", "productURL", "imageURL", "source","sourceLogo"])
    
    # Tri des résultats par prix décroissant
    def parse_price(price_str):
        try:
            # Suppression du suffixe et des séparateurs
            return float(price_str.replace(" FCFA", "").replace(",", ""))
        except Exception:
            return 0.0

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
    df = scrape_amazon(query)
    if df is not None and not df.empty:
        records = df.to_dict(orient="records")
        return jsonify(records)
    else:
        return jsonify({"error": "Aucune donnée extraite."}), 404

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
