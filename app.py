import logging
import sqlite3
import random
import math
import smtplib
import re
from flask import Flask, jsonify, request, session
from flask_cors import CORS
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
from concurrent.futures import ThreadPoolExecutor
import pyrebase

# --- Fonctions de scraping
from scrapers.amazon_scraper import scrape_amazon
from scrapers.glotehlo_scraper import scrape_glotelho
from scrapers.walmart_scraper import scrape_walmart

# --- Configuration de Firebase pour l'authentification ---
firebaseConfig = {
    "apiKey": "AIzaSyCY2J3AIlermD2ZmdHh_Kq5VK62USxlGp8",
    "authDomain": "apple-stock-prediction.firebaseapp.com",
    "projectId": "apple-stock-prediction",
    "storageBucket": "apple-stock-prediction.appspot.com",
    "messagingSenderId": "830704283155",
    "appId": "1:830704283155:web:b399f596fe2201374859e6",
    "measurementId": "G-Z5QKF0PJNV",
    "databaseURL": "https://apple-stock-prediction-default-rtdb.firebaseio.com/"
}

firebase = pyrebase.initialize_app(firebaseConfig)
firebase_auth = firebase.auth()

# --- Configuration de l'application Flask ---
app = Flask(__name__)
app.secret_key = "votre_secret_key"  # À modifier pour la production
CORS(app, supports_credentials=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


#########################
# Initialisation de la BDD pour les abonnements
#########################
def init_db() -> None:
    """Initialise la base de données SQLite pour les abonnements."""
    try:
        with sqlite3.connect("subscriptions.db") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_url TEXT,
                    initial_price REAL,
                    email TEXT
                )
            """)
            conn.commit()
        logging.info("Base de données initialisée avec succès.")
    except Exception as e:
        logging.error("Erreur lors de l'initialisation de la BDD: %s", e)


init_db()


#########################
# Fonctions utilitaires pour traiter les résultats et les prix
#########################
def normalize_text(text: str) -> str:
    """Normalise le texte en le mettant en minuscule et en supprimant les caractères non alphanumériques."""
    return re.sub(r'\W+', '', text.lower())


def extract_price(price_str: str) -> float:
    """
    Extrait et convertit le prix à partir d'une chaîne.
    Retourne float('inf') en cas d'erreur.
    """
    try:
        cleaned = price_str.replace(" FCFA", "").replace(",", "").replace(" ", "").strip()
        return float(cleaned)
    except Exception as e:
        logging.error("Erreur d'extraction du prix pour '%s': %s", price_str, e)
        return float('inf')


def format_price(price_value: float) -> str:
    """Formate une valeur de prix en chaîne avec 2 décimales et le suffixe FCFA."""
    try:
        return "{:,.2f} FCFA".format(price_value)
    except Exception as e:
        logging.error("Erreur de formatage pour le prix %s: %s", price_value, e)
        return "N/A"


def compute_deal_attributes(record: dict) -> dict:
    """Calcule et ajoute les attributs de l'offre dans le dictionnaire."""
    numeric_price = extract_price(record.get("price", ""))
    record["numeric_price"] = numeric_price
    record["price"] = format_price(numeric_price)
    return record


#########################
# Fonction de recherche de produits
#########################
def do_search(query: str) -> list:
    """
    Effectue une recherche de produits à partir d'un mot-clé en utilisant plusieurs scrapers.
    Retourne une liste de produits filtrés et triés par prix.
    """
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_amazon = executor.submit(scrape_amazon, query)
            future_glotehlo = executor.submit(scrape_glotelho, query)
            future_walmart = executor.submit(scrape_walmart, query)
            amazon_df = future_amazon.result()
            glotehlo_records = future_glotehlo.result()
            walmart_df = future_walmart.result()

        amazon_records = amazon_df.to_dict(orient="records") if (amazon_df is not None and not amazon_df.empty) else []
        walmart_records = walmart_df.to_dict(orient="records") if (walmart_df is not None and not walmart_df.empty) else []
        combined_results = amazon_records + glotehlo_records + walmart_records

        filtered_results = []
        seen_urls = set()
        for record in combined_results:
            if "price" not in record or not record["price"] or record["price"] == "N/A":
                continue
            numeric_price = extract_price(record["price"])
            if numeric_price == float('inf') or numeric_price == 0.0 or math.isnan(numeric_price):
                continue
            product_url = record.get("productURL", "").strip()
            if product_url:
                if product_url in seen_urls:
                    continue
                seen_urls.add(product_url)
            record = compute_deal_attributes(record)
            filtered_results.append(record)
        sorted_results = sorted(filtered_results, key=lambda r: r["numeric_price"])
        for record in sorted_results:
            record.pop("numeric_price", None)
        return sorted_results
    except Exception as e:
        logging.error("Erreur dans do_search: %s", e)
        raise


#########################
# Endpoints d'authentification
#########################
@app.route('/register', methods=['POST'])
def register():
    """
    Crée un nouvel utilisateur via Firebase.
    Retourne un message de succès ou une erreur.
    """
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis."}), 400
    try:
        user = firebase_auth.create_user_with_email_and_password(email, password)
        session['email'] = email
        logging.info("Compte créé pour %s", email)
        return jsonify({"message": "Compte créé avec succès.", "email": email})
    except Exception as e:
        logging.error("Erreur lors de la création du compte : %s", e)
        if "EMAIL_EXISTS" in str(e):
            return jsonify({"error": "Un compte avec cet e-mail existe déjà."}), 400
        return jsonify({"error": "Erreur lors de la création du compte."}), 500


@app.route('/login', methods=['POST'])
def login():
    """
    Connecte un utilisateur en vérifiant ses identifiants via Firebase.
    Stocke l'email et le token dans la session.
    """
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis."}), 400
    try:
        user = firebase_auth.sign_in_with_email_and_password(email, password)
        session['email'] = email
        session['idToken'] = user.get('idToken')
        logging.info("Utilisateur connecté : %s", email)
        return jsonify({"message": "Connexion réussie.", "email": email})
    except Exception as e:
        logging.error("Erreur lors de la connexion : %s", e)
        if "EMAIL_NOT_FOUND" in str(e):
            return jsonify({"error": "Aucun compte trouvé avec cet email."}), 400
        elif "INVALID_PASSWORD" in str(e):
            return jsonify({"error": "Mot de passe incorrect."}), 400
        return jsonify({"error": "Erreur lors de la connexion."}), 500


@app.route('/logout', methods=['POST'])
def logout():
    """Déconnecte l'utilisateur en effaçant la session."""
    session.clear()
    logging.info("Utilisateur déconnecté.")
    return jsonify({"message": "Déconnexion réussie."})


@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    """Envoie un email de réinitialisation de mot de passe via Firebase."""
    data = request.get_json()
    email = data.get("email")
    if not email:
        return jsonify({"error": "Email requis."}), 400
    try:
        firebase_auth.send_password_reset_email(email)
        logging.info("Email de réinitialisation envoyé à %s", email)
        return jsonify({"message": "Email de réinitialisation envoyé."})
    except Exception as e:
        logging.error("Erreur lors de la réinitialisation du mot de passe : %s", e)
        return jsonify({"error": "Erreur lors de l'envoi de l'email."}), 500


#########################
# Endpoint /search : Recherche de produits
#########################
@app.route('/search', methods=['GET'])
def search():
    """
    Recherche des produits selon un mot-clé.
    Stocke la requête dans la session pour usage ultérieur dans /subscribe.
    """
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    session["last_search_query"] = query
    try:
        results = do_search(query)
        logging.info("Recherche '%s' retournant %d résultats.", query, len(results))
        return jsonify(results)
    except Exception as e:
        logging.error("Erreur dans /search: %s", e)
        return jsonify({"error": str(e)}), 500


#########################
# Endpoint /subscribe : Abonnement aux produits de la dernière recherche
#########################
@app.route('/subscribe', methods=['POST'])
def subscribe():
    """
    Enregistre un abonnement pour un query donné.
    Nécessite que l'email soit présent dans la session (login requis)
    et que le query soit envoyé dans le corps de la requête.
    Exemple du corps JSON : {"query": "macbook"}
    """
    data = request.get_json()
    query = data.get("query") if data else None
    email = session.get("email")
    if not query or not email:
        return jsonify({"error": "L'email en session et le query en paramètre sont requis (login et query requis)."}), 400
    try:
        results = do_search(query)
        if not results:
            return jsonify({"message": "Aucun produit trouvé pour la requête."}), 404

        with sqlite3.connect("subscriptions.db") as conn:
            cursor = conn.cursor()
            count = 0
            for record in results:
                product_url = record.get("productURL", "").strip()
                initial_price = extract_price(record.get("price", ""))
                if product_url and initial_price != float('inf'):
                    cursor.execute("INSERT INTO subscriptions (product_url, initial_price, email) VALUES (?, ?, ?)",
                                   (product_url, initial_price, email))
                    count += 1
            conn.commit()
        logging.info("Abonnement enregistré pour le query '%s' pour %s (%d produits).", query, email, count)
        return jsonify({"message": f"Abonnement enregistré pour {count} produits.", "count": count})
    except Exception as e:
        logging.error("Erreur dans /subscribe: %s", e)
        return jsonify({"error": str(e)}), 500

#########################
# Fonction de simulation du prix actuel d'un produit
#########################
def get_current_price(product_url: str) -> float:
    """
    Simule la récupération du prix actuel d'un produit.
    Pour une application réelle, implémentez la logique de scraping ou d'API.
    """
    try:
        price = random.uniform(1000, 10000)
        return price
    except Exception as e:
        logging.error("Erreur lors de la simulation du prix pour '%s': %s", product_url, e)
        return float('inf')


#########################
# Fonction d'envoi d'email d'alerte
#########################
def send_email_alert(email: str, product_url: str, current_price: float) -> None:
    """
    Envoie un email d'alerte lorsque le prix d'un produit a baissé.
    Utilise le serveur SMTP de Sendinblue.
    """
    smtp_server = "smtp-relay.sendinblue.com"
    smtp_port = 587
    smtp_username = "gunwaterco@gmail.com"
    smtp_password = "JmYtx390OGUBzWgn"
    from_email = "shopwise@gmail.com"
    subject = "Alerte: Le prix de votre produit a baissé!"
    body = (f"Bonjour,\n\nLe prix de l'article suivant a baissé : {product_url}\n"
            f"Nouveau prix : {format_price(current_price)}\n\nCordialement,\nVotre équipe")
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        logging.info("Email envoyé à %s", email)
    except Exception as e:
        logging.error("Erreur lors de l'envoi de l'email à %s: %s", email, e)


#########################
# Fonction de vérification des prix et mise à jour des abonnements
#########################
def run_price_check() -> list:
    """
    Vérifie si le prix des produits abonnés a baissé.
    Envoie un email d'alerte et met à jour la BDD pour chaque produit concerné.
    Retourne la liste des alertes déclenchées.
    """
    try:
        with sqlite3.connect("subscriptions.db") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, product_url, initial_price, email FROM subscriptions")
            subscriptions = cursor.fetchall()
            alerts_triggered = []
            for sub in subscriptions:
                sub_id, product_url, baseline_price, email = sub
                current_price = get_current_price(product_url)
                if current_price < baseline_price:
                    send_email_alert(email, product_url, current_price)
                    alerts_triggered.append({
                        "subscription_id": sub_id,
                        "email": email,
                        "product_url": product_url,
                        "current_price": format_price(current_price),
                        "previous_price": format_price(baseline_price)
                    })
                    cursor.execute("UPDATE subscriptions SET initial_price = ? WHERE id = ?", (current_price, sub_id))
            conn.commit()
        logging.info("Vérification terminée. Alertes déclenchées : %s", alerts_triggered)
        return alerts_triggered
    except Exception as e:
        logging.error("Erreur lors de la vérification des prix: %s", e)
        return []


@app.route('/check_prices', methods=['GET'])
def check_prices():
    """
    Endpoint manuel pour vérifier les prix des produits abonnés.
    Retourne un message et la liste des articles dont le prix a changé.
    """
    alerts = run_price_check()
    if alerts is None:
        return jsonify({"error": "Erreur lors de la vérification des prix."}), 500
    return jsonify({
        "message": "Vérification manuelle effectuée. Consultez les logs pour plus d'informations.",
        "alerts_triggered": alerts
    })


#########################
# Lancement de l'application et du job planifié
#########################
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=run_price_check, trigger="interval", hours=2)
    scheduler.start()
    try:
        app.run(debug=True, host="0.0.0.0", port=5000)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
