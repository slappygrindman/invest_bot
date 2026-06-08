import os
import requests
import yfinance as ticker_data  # ou ton code actuel

# 1. Récupération des secrets depuis l'environnement
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def envoyer_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    response = requests.post(url, json=payload)
    return response.json()


# 2. Ta logique actuelle avec yfinance
def main():
    # Exemple simple, remplace par ta logique yfinance
    msft = ticker_data.Ticker("MSFT")
    prix = msft.history(period="1d")["Close"].iloc[-1]

    rapport = f"📊 *Rapport Financier*\nMicrosoft (MSFT) : {prix:.2f} USD"

    # 3. Envoi du message
    envoyer_telegram(rapport)


if __name__ == "__main__":
    main()