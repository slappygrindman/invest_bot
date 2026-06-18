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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    response = requests.post(url, json=payload)
    return response.json()


# 2. Analyse complète des ETF
def get_investment_analysis() -> str:
    etfs = ["PAEEM.PA", "WPEA.PA","LYTR.DE"]
    data = {}

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
                log_square[:(i+moit)]
                vola_6m.append(vola)

            coef = vola_6m[-1]/np.median(vola_6m)
            max_6 = max(close[moit:])
            pourc_haut_6m = (close[-1]-max_6)/max_6

            print(f"\n========== {ticker} ==========")
            print(f"\ncoef :", round(coef,3))
            print(f"\nseuil * coef :", round(coef*seuil*100,3),"%")
            print(f"\nmax_6", round(max_6,3))
            print(f"\nprix", round(close[-1],3))
            print(f"\nPar rapport au plus haut à 6 mois :", round(pourc_haut_6m,3),"%")

            print(f"\n===== Conseil pour {ticker} =====")

            if pourc_haut_6m < (seuil*coef) :
                print(f"\nAchat 💲​")
            else :
                print(f"\nAttendre ⏳​")

    finally:
        sys.stdout = original_stdout

    return output.getvalue()


# 3. Envoi en découpant si > 4000 caractères (limite Telegram)
def main():
    analyse = get_investment_analysis()

    chunks = [analyse[i:i+4000] for i in range(0, len(analyse), 4000)]
    for chunk in chunks:
        envoyer_telegram(chunk)


if __name__ == "__main__":
    main()
