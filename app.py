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

# --- Fonctions scrapping
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
def init_db():
    conn = sqlite3.connect("subscriptions.db")
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
    conn.close()

init_db()

#########################
# Fonctions utilitaires pour traiter les résultats et les prix
#########################
def normalize_text(text):
    """Normalise le texte en le mettant en minuscule et en supprimant les caractères non alphanumériques."""
    return re.sub(r'\W+', '', text.lower())

def extract_price(price_str):
    try:
        cleaned = price_str.replace(" FCFA", "").replace(",", "").replace(" ", "").strip()
        return float(cleaned)
    except Exception:
        return float('inf')

def format_price(price_value):
    return "{:,.2f} FCFA".format(price_value)

def compute_deal_attributes(record):
    numeric_price = extract_price(record.get("price", ""))
    record["numeric_price"] = numeric_price
    record["price"] = format_price(numeric_price)
    return record

#########################
# Fonction de recherche (sans filtre sur le titre)
#########################
def do_search(query):
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
    # On ne filtre plus selon le titre (query) ici
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

#########################
# Endpoints d'authentification
#########################
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis."}), 400
    try:
        user = firebase_auth.create_user_with_email_and_password(email, password)
        session['email'] = email
        logging.info(f"Compte créé pour {email}")
        return jsonify({"message": "Compte créé avec succès.", "email": email})
    except Exception as e:
        logging.error(f"Erreur lors de la création du compte : {e}")
        if "EMAIL_EXISTS" in str(e):
            return jsonify({"error": "Un compte avec cet e-mail existe déjà."}), 400
        return jsonify({"error": "Erreur lors de la création du compte."}), 500

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis."}), 400
    try:
        user = firebase_auth.sign_in_with_email_and_password(email, password)
        session['email'] = email
        session['idToken'] = user['idToken']
        logging.info(f"Utilisateur connecté : {email}")
        return jsonify({"message": "Connexion réussie.", "email": email})
    except Exception as e:
        logging.error(f"Erreur lors de la connexion : {e}")
        if "EMAIL_NOT_FOUND" in str(e):
            return jsonify({"error": "Aucun compte trouvé avec cet email."}), 400
        elif "INVALID_PASSWORD" in str(e):
            return jsonify({"error": "Mot de passe incorrect."}), 400
        return jsonify({"error": "Erreur lors de la connexion."}), 500

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    logging.info("Utilisateur déconnecté.")
    return jsonify({"message": "Déconnexion réussie."})

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get("email")
    if not email:
        return jsonify({"error": "Email requis."}), 400
    try:
        firebase_auth.send_password_reset_email(email)
        logging.info(f"Email de réinitialisation envoyé à {email}")
        return jsonify({"message": "Email de réinitialisation envoyé."})
    except Exception as e:
        logging.error(f"Erreur lors de la réinitialisation du mot de passe : {e}")
        return jsonify({"error": "Erreur lors de l'envoi de l'email."}), 500

#########################
# Endpoint /search : recherche de produits
#########################
@app.route('/search', methods=['GET'])
def search():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "Veuillez fournir un mot-clé via le paramètre 'query'."}), 400
    # Stocke la requête dans la session pour l'utiliser dans /subscribe
    session["last_search_query"] = query
    try:
        results = do_search(query)
        logging.info("Recherche '%s' retournant %d résultats.", query, len(results))
        return jsonify(results)
    except Exception as e:
        logging.error(f"Erreur dans /search: {e}")
        return jsonify({"error": str(e)}), 500

#########################
# Endpoint /subscribe : abonnement basé sur la dernière recherche
#########################
@app.route('/subscribe', methods=['POST'])
def subscribe():
    query = session.get("last_search_query")
    email = session.get("email")
    if not query or not email:
        return jsonify({"error": "La requête et l'email doivent être présents dans la session (login et recherche requis)."}), 400
    try:
        results = do_search(query)
        if not results:
            return jsonify({"message": "Aucun produit trouvé pour la requête."}), 404

        conn = sqlite3.connect("subscriptions.db")
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
        conn.close()
        logging.info("Abonnement enregistré pour la requête '%s' pour %s (%d produits).", query, email, count)
        return jsonify({"message": f"Abonnement enregistré pour {count} produits.", "count": count})
    except Exception as e:
        logging.error("Erreur dans /subscribe: %s", e)
        return jsonify({"error": str(e)}), 500

#########################
# Fonction simulant la récupération du prix actuel d'un produit
#########################
def get_current_price(product_url):
    return random.uniform(1000, 10000)

#########################
# Fonction d'envoi d'email
#########################
def send_email_alert(email, product_url, current_price):
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
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
        server.quit()
        logging.info("Email envoyé à %s", email)
    except Exception as e:
        logging.error("Erreur lors de l'envoi de l'email: %s", e)

def run_price_check():
    try:
        conn = sqlite3.connect("subscriptions.db")
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
        conn.close()
        logging.info("Vérification terminée. Alertes déclenchées : %s", alerts_triggered)
        return alerts_triggered
    except Exception as e:
        logging.error("Erreur lors de la vérification des prix: %s", e)
        return None

@app.route('/check_prices', methods=['GET'])
def check_prices():
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
