import undetected_chromedriver as uc  # Contourne les protections anti-bot d'Amazon
from bs4 import BeautifulSoup  # Analyse HTML pour extraire les données
from selenium.webdriver.chrome.options import Options  # Configuration du navigateur Selenium
from selenium.webdriver.common.by import By  # Sélecteurs d'éléments
from selenium.webdriver.support.wait import WebDriverWait  # Attente d'éléments spécifiques
from selenium.webdriver.support import expected_conditions as EC  # Conditions d'attente
import time  # Gestion des pauses
from fake_useragent import UserAgent  # Génère des user-agents aléatoires pour éviter les blocages
from selenium.common.exceptions import NoSuchElementException, TimeoutException  # Gestion des erreurs Selenium
from flask import Flask, jsonify, request  # API Flask
import concurrent.futures  # Exécution concurrente pour accélérer le scraping

# Initialisation de l'application Flask
app = Flask(__name__)

def get_url(search_term, page=1):
    """Génère l'URL de recherche Amazon pour le mot-clé donné et la page spécifiée."""
    template = "https://www.amazon.com/s?k={}&page={}"
    search_term = search_term.replace(" ", "+")  # Formatage du mot-clé pour l'URL
    return template.format(search_term, page)

def scrape_records(item):
    """
    Extrait les informations d'un seul produit en tenant compte de la nouvelle structure HTML.
    On récupère :
      - la description et l'URL du produit depuis le conteneur data-cy="title-recipe",
      - le prix depuis le conteneur data-cy="price-recipe",
      - la note depuis le span "a-icon-alt",
      - le nombre d'avis depuis le conteneur data-cy="reviews-block",
      - l'URL de l'image depuis l'élément <img class="s-image">,
      - et la source fixe "Amazon".
    """
    try:
        # Description et URL du produit
        title_container = item.select_one("div[data-cy='title-recipe']")
        if title_container:
            a_tag = title_container.find("a")
            if a_tag:
                description = a_tag.get_text(strip=True)
                product_url = "https://amazon.com" + a_tag.get("href")
            else:
                description, product_url = "N/A", "N/A"
        else:
            description, product_url = "N/A", "N/A"
        
        # Prix du produit
        price_container = item.select_one("div[data-cy='price-recipe'] span.a-offscreen")
        price = price_container.get_text(strip=True) if price_container else "N/A"
        
        # Note (rating)
        rating_container = item.select_one("span.a-icon-alt")
        rating = rating_container.get_text(strip=True) if rating_container else "No Rating"
        
        # Nombre d'avis (review count)
        review_container = item.select_one("div[data-cy='reviews-block'] a.s-underline-text")
        review_count = review_container.get_text(strip=True) if review_container else "0"
        
        # URL de l'image du produit
        image_container = item.find("img", class_="s-image")
        image_url = image_container.get("src") if image_container else "N/A"
        
        return {
            "description": description,
            "price": price,
            "rating": rating,
            "review_count": review_count,
            "product_url": product_url,
            "image_url": image_url,
            "source": "Amazon"
        }
    except Exception as e:
        print(f"❌ Erreur lors de l'extraction d'un produit : {e}")
        return None

def scrape_amazon_page(search_term, page):
    """Scrape une page spécifique de résultats Amazon."""
    ua = UserAgent()
    options = Options()
    options.add_argument(f"user-agent={ua.random}")
    options.add_argument("--headless")  # Mode sans affichage
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = uc.Chrome(options=options)
    url = get_url(search_term, page)
    print(f"🔍 Chargement de la page {page}: {url}")
    driver.get(url)
    
    # Attente que le contenu principal soit chargé
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.s-main-slot"))
        )
    except TimeoutException:
        print(f"⚠ Timeout sur la page {page}.")
        driver.quit()
        return []
    
    # Défilement pour forcer le chargement dynamique des éléments
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    
    # Analyse du code HTML avec BeautifulSoup
    soup = BeautifulSoup(driver.page_source, "html.parser")
    results = soup.find_all("div", {"data-component-type": "s-search-result"})
    
    # Extraction des informations pour chaque produit
    page_records = []
    for item in results:
        record = scrape_records(item)
        if record:
            page_records.append(record)
    
    driver.quit()  # Fermeture du navigateur
    print(f"✅ Page {page} : {len(page_records)} produits trouvés.")
    return page_records

def scrape_amazon(search_term, max_pages=5):
    """Scrape plusieurs pages en parallèle et retourne l'ensemble des résultats."""
    all_records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_pages) as executor:
        futures = [
            executor.submit(scrape_amazon_page, search_term, page)
            for page in range(1, max_pages + 1)
        ]
        for future in concurrent.futures.as_completed(futures):
            records = future.result()
            all_records.extend(records)
    return all_records

@app.route("/search", methods=["GET"])
def search():
    """API Flask pour récupérer les produits Amazon sous format JSON."""
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400

    try:
        max_pages = int(request.args.get("max_pages", 3))
    except ValueError:
        max_pages = 3

    print(f"🔍 Recherche lancée : {query} sur {max_pages} pages.")
    records = scrape_amazon(query, max_pages)
    return jsonify(records)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
