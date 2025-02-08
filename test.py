import undetected_chromedriver as uc

try:
    driver = uc.Chrome(version_main=120)
    print("✅ Chrome lancé avec succès !")
    driver.quit()
except Exception as e:
    print(f"❌ Erreur avec undetected_chromedriver : {e}")
