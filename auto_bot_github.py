import os
import requests
import yfinance as yf
import numpy as np
import io
import sys

# 1. Récupération des secrets depuis l'environnement
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def envoyer_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ ERREUR: BOT_TOKEN ou CHAT_ID manquants!")
        return False
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    
    try:
        response = requests.post(url, json=payload)
        result = response.json()
        
        if result.get("ok"):
            print(f"✅ Message envoyé à {CHAT_ID}")
            return True
        else:
            print(f"❌ Erreur Telegram: {result.get('description')}")
            return False
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi: {e}")
        return False


# 2. Analyse complète des ETF
def get_investment_analysis() -> str:
    etfs = ["PAEEM.PA", "WPEA.PA","LYTR.DE"]

    output = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = output

    try:
        for ticker in etfs:
            df = yf.download(ticker, period="252d", progress=False)
            close = df["Close"].squeeze()

            # ÉTAPE 1 : Rendements journaliers
            close_list = close.tolist()
            seuil = -0.07

            moit = int(len(close_list)/2)

            log_square = []
            for i in range(1, len(close_list)):
                prix_veille = close_list[i - 1]
                prix_du_jour = close_list[i]
                log_rdm_carre = (np.log(prix_du_jour / prix_veille))**2
                log_square.append(log_rdm_carre)

            vola_6m = []

            for i in range(moit):
                vola = np.sqrt(np.sum(log_square[i:(i+moit)])/
                            moit)*np.sqrt(252)
                vola_6m.append(vola)

            coef = vola_6m[-1]/np.median(vola_6m)
            max_6 = max(close[moit:])
            pourc_haut_6m = ((close.iloc[-1]-max_6)/max_6)*100

            print(f"\n========== {ticker} ==========")
            print("Coef :", round(coef,3))
            print("Seuil :", (seuil*100),"%")
            print("seuil * coef :", round(coef*seuil*100,3),"%")
            print("Plus haut à 6 mois :", round(max_6,3))
            print("Prix :", round(close.iloc[-1],3))
            print("Par rapport au plus haut à 6 mois :", round(pourc_haut_6m,3),"%")

            if pourc_haut_6m < (seuil*coef) :
                print("Achat 💲​")
            else :
                print("Attendre ⏳​")

    finally:
        sys.stdout = original_stdout

    return output.getvalue()


# 3. Envoi en découpant si > 4000 caractères (limite Telegram)
def main():
    analyse = get_investment_analysis()
    print(analyse)  # ← affiche aussi dans les logs
    
    chunks = [analyse[i:i+4000] for i in range(0, len(analyse), 4000)]
    for chunk in chunks:
        if not envoyer_telegram(chunk):
            print(f"⚠️  Impossible d'envoyer un chunk")


if __name__ == "__main__":
    main()
