TICKERS = {
    "wld": "CW8.PA",
    "eme": "PAEEM.PA",
    "como": "CMSE.PA"}
S1 = 0.05
S2 = 0.15
S3 = 0.21

import os
import json
import requests
import sys
import yfinance as yf
import matplotlib.pyplot as plt
import numpy as np

TOKEN = os.environ.get("GRAPH_TOKEN")
BASE = f"https://api.telegram.org/bot{TOKEN}"
OFFSET_FILE = "offset.txt"

# -----------------------------

def get_offset():
    
    if os.path.exists(OFFSET_FILE):
        with open(OFFSET_FILE) as f:
            return int(f.read())

    return 0

def save_offset(offset):

    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

def send_photo(chat_id, filename):

    with open(filename, "rb") as photo:

        requests.post(
            f"{BASE}/sendPhoto",
            data={"chat_id": chat_id},
            files={"photo": photo},
        )


def send_message(chat_id, text):

    requests.post(
        f"{BASE}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
        },
    )

def make_curve(code, days):

    ticker = TICKERS.get(code.lower())

    if ticker is None:
        return None

    data = yf.download(
        ticker,
        period=f"{days}d",
        progress=False,
        auto_adjust=True,
    )
    print("yfinance a touné")
    closes = data["Close"].squeeze().tolist()
    window = 126
    closes_max_6 = [
        np.max(closes[max(0, i - window + 1):i + 1])
        for i in range(len(closes))
    ]
    closes_S1 = [x * (1-S1) for x in closes_max_6]
    closes_S2 = [x * (1-S2) for x in closes_max_6]
    closes_S3 = [x * (1-S3) for x in closes_max_6]
    
    if data.empty:
        return None

    plt.figure(figsize=(10,5))
    plt.plot(data.index, data["Close"], linewidth=2, color="blue")
    plt.plot(data.index, closes_max_6, linewidth=1, color="green")
    plt.plot(data.index, closes_S1, linewidth=1, color="orange")
    plt.plot(data.index, closes_S2, linewidth=1, color="red")
    plt.plot(data.index, closes_S3, linewidth=1, color="purple")
    plt.grid(True)
    plt.title(f"{code.upper()} - {days} jours")
    plt.tight_layout()
    filename = "curve.png"
    plt.savefig(filename)
    plt.close()

    return filename

def process(update):

    chat_id = update["message"]["chat"]["id"]
    text = update["message"].get("text","")
    args = text.split()
    if len(args) == 0:
        return

    if args[0] == "/help":

        send_message(
            chat_id,
            "/curve paeem 126\n/list",
        )

        return


    if args[0] == "/list":

        txt = "\n".join(sorted(TICKERS.keys()))

        send_message(chat_id, txt)

        return


    if args[0] == "/curve":

        if len(args) != 3:

            send_message(
                chat_id,
                "Exemple : /curve paeem 126",
            )

            return

        code = args[1]

        try:
            days = int(args[2])
        except:
            send_message(chat_id, "Nombre invalide.")
            return

        file = make_curve(code, days)

        if file is None:

            send_message(
                chat_id,
                "ETF inconnu.",
            )

            return

        send_photo(chat_id, file)

        return


# -----------------------------

def main():

    offset = get_offset()

    r = requests.get(
        f"{BASE}/getUpdates",
        params={
            "offset": offset + 1,
            "timeout": 0,
        },
    )

    #updates = r.json()["result"]

    response = r.json()

    print(response)

    if not response.get("ok"):
        print("Erreur Telegram :", response)
        return
              
    updates = response["result"]

    if len(updates) == 0:
        return

    for update in updates:

        process(update)

        save_offset(update["update_id"])


if __name__ == "__main__":
    main()
