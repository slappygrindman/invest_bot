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
    etfs = ["PAEEM.PA", "WPEA.PA"]
    data = {}

    output = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = output

    try:
        for ticker in etfs:
            df = yf.download(ticker, period="1y", progress=False)
            close = df["Close"].squeeze()

            # ÉTAPE 1 : Rendements journaliers
            close_list = close.tolist()
            returns_list = []
            for i in range(1, len(close_list)):
                prix_veille = close_list[i - 1]
                prix_du_jour = close_list[i]
                log_rdm_carre = (np.log(prix_du_jour / prix_veille)**2)
                returns_list.append(log_rdm_carre)

            # ÉTAPE 2 : Moyenne des rendements
            volatilite_annuelle = np.std(returns_list)*np.sqrt(252)

            df["volatility"] = volatilite_annuelle

            # MA200
            df["MA200"] = close.rolling(window=200).mean()

            # Momentum 6 mois
            df["Momentum6M"] = (close / close.shift(126) - 1)

            # plus haut à 6 mois
            haut_6 = max(close[-126:])

            # plus haut à 3 mois
            haut_3 = max(close[-63:])

            data[ticker] = df

            # Valeurs finales
            price    = df["Close"].to_numpy().flatten()[-1]
            ma200    = df["MA200"].to_numpy().flatten()[-1]
            vola     = df["volatility"].to_numpy().flatten()[-1]
            momentum = df["Momentum6M"].to_numpy().flatten()[-1]

            print(f"\n========== {ticker} ==========")
            print(f"Prix = {round(price, 2)}")
            print(f"Volatilité : {round(vola, 2)}%")
            print(f"MA200 = {round(ma200, 2)}")
            print(f"Plus haut 6 mois : ",haut_6)
            print(f"Plus haut 3 mois : ",haut_3)

            if price > ma200:
                print("Tendance MA200 : HAUSSIERE 🟢")
            else:
                print("Tendance MA200 : BAISSIERE 🔴")

            if momentum > 0:
                print(f"Momentum 6 mois : {round(momentum * 100, 2)}% POSITIF 🟢")
            else:
                print(f"Momentum 6 mois : {round(momentum * 100, 2)}% NEGATIF 🔴")

            print(f"\n===== Conseil pour {ticker} =====")



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
