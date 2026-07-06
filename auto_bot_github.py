import os
import json
import requests
import yfinance as yf
import numpy as np
import io
import sys
from datetime import date

# 1. Récupération des secrets depuis l'environnement
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "state.json"

# Seuils de drawdown (avant ajustement par le coefficient de volatilité)
SEUILS = {
    "T1": -0.05,
    "T2": -0.15,
    "T3": -0.21,
}

# Répartition de l'enveloppe tactique (20% du montant mensuel) entre les seuils
POIDS = {
    "T1": 0.20,
    "T2": 0.30,
    "T3": 0.50,
}


def envoyer_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ ERREUR: BOT_TOKEN ou CHAT_ID manquants!")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        result = response.json()

        if result.get("ok"):
            print(f"✅ Message envoyé")
            return True
        else:
            print(f"❌ Erreur Telegram: {result.get('description')}")
            return False
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi: {e}")
        return False


# 2. Gestion de l'état persistant (quels seuils ont déjà servi ce mois-ci)
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_mois_actuel():
    return date.today().strftime("%Y-%m")


def verifier_seuils(ticker, pourc_haut_6m, coef, state):
    """
    Compare le drawdown actuel aux seuils (ajustés par le coefficient de vol),
    renvoie la liste des seuils NOUVELLEMENT franchis ce mois-ci, et met à
    jour `state` en mémoire (state est sauvegardé une seule fois à la fin).
    """
    mois = get_mois_actuel()

    if ticker not in state or state[ticker].get("mois") != mois:
        # Nouveau mois (ou ticker jamais vu) -> on réinitialise ses compteurs
        state[ticker] = {
            "mois": mois,
            "declenches": {k: False for k in SEUILS},
        }

    nouveaux = []
    for label, seuil in SEUILS.items():
        seuil_ajuste = seuil * coef
        deja_fait = state[ticker]["declenches"].get(label, False)
        if pourc_haut_6m <= seuil_ajuste and not deja_fait:
            nouveaux.append({
                "label": label,
                "seuil_ajuste": seuil_ajuste,
                "poids": POIDS[label],
            })
            state[ticker]["declenches"][label] = True

    return nouveaux


# 3. Analyse complète des ETF
def get_investment_analysis():
    etfs = ["PAEEM.PA", "WPEA.PA", "CMSE.PA"]

    state = load_state()
    signaux = []  # messages d'alerte pour les seuils nouvellement franchis

    output = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = output

    try:
        for ticker in etfs:
            df = yf.download(ticker, period="252d", progress=False)
            close = df["Close"].squeeze()

            close_list = close.tolist()
            moit = int(len(close_list) / 2)

            log_square = []
            for i in range(1, len(close_list)):
                prix_veille = close_list[i - 1]
                prix_du_jour = close_list[i]
                log_rdm_carre = (np.log(prix_du_jour / prix_veille)) ** 2
                log_square.append(log_rdm_carre)

            vola_6m = []
            for i in range(moit):
                vola = np.sqrt(np.sum(log_square[i:(i + moit)]) / moit) * np.sqrt(252)
                vola_6m.append(vola)

            coef = vola_6m[-1] / np.median(vola_6m)
            max_6 = max(close[moit:])
            min_6 = min(close[moit:])
            pourc_haut_6m = ((close.iloc[-1] - max_6) / max_6)

            print(f"\n========== {ticker} ==========")
            print("Coef :", round(coef, 3))
            print("Plus haut à 6 mois :", round(max_6, 3))
            print("Plus bas à 6 mois :", round(min_6, 3))
            print("Prix :", round(close.iloc[-1], 3))
            print("Drawn down 6m :", round(pourc_haut_6m * 100, 3), "%")
            for label, seuil in SEUILS.items():
                deja_fait = state.get(ticker, {}).get("declenches", {}).get(label, False)
                statut = "déjà utilisé ce mois" if deja_fait else "disponible"
                print(f"Seuil {label} ajusté : {round(seuil * coef * 100, 2)}% ({statut})")

            nouveaux = verifier_seuils(ticker, pourc_haut_6m, coef, state)
            for n in nouveaux:
                msg = (
                    f"🟢 SIGNAL D'ACHAT — {ticker}\n"
                    f"Seuil {n['label']} franchi ({round(n['seuil_ajuste'] * 100, 2)}%)\n"
                    f"Part de l'enveloppe tactique : {int(n['poids'] * 100)}%\n"
                    f"Drawdown actuel : {round(pourc_haut_6m * 100, 2)}%"
                )
                signaux.append(msg)
                print(f"\n{msg}")

    finally:
        sys.stdout = original_stdout

    save_state(state)
    return output.getvalue(), signaux


# 4. Envoi des messages (analyse + signaux séparés)
def main():
    analyse, signaux = get_investment_analysis()
    print(analyse)  # affiche aussi dans les logs GitHub Actions

    # Analyse complète, découpée si > 4000 caractères (limite Telegram)
    chunks = [analyse[i:i + 4000] for i in range(0, len(analyse), 4000)]
    for chunk in chunks:
        if not envoyer_telegram(chunk):
            print("⚠️  Impossible d'envoyer un chunk d'analyse")

    # Signaux d'achat nouvellement déclenchés (un message dédié par signal)
    for signal in signaux:
        if not envoyer_telegram(signal):
            print("⚠️  Impossible d'envoyer un signal d'achat")


if __name__ == "__main__":
    main()
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
    payload = {
        "chat_id": CHAT_ID, 
        "text": message
        # ← enlever: "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        result = response.json()
        
        if result.get("ok"):
            print(f"✅ Message envoyé")
            return True
        else:
            print(f"❌ Erreur Telegram: {result.get('description')}")
            return False
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi: {e}")
        return False


# 2. Analyse complète des ETF
def get_investment_analysis() -> str:
    etfs = ["PAEEM.PA", "WPEA.PA","LYTR.DE","CMSE.PA"]

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
            min_6 = min(close[moit:])
            pourc_haut_6m = ((close.iloc[-1]-max_6)/max_6)

            print(f"\n========== {ticker} ==========")
            print("Coef :", round(coef,3))
            print("Seuil :", round((seuil*100),1),"%")
            print("Seuil avec Coef :", round(coef*seuil*100,3),"%")
            print("Plus haut à 6 mois :", round(max_6,3))
            print("Plus bas à 6 mois :", round(min_6,3))
            print("Prix :", round(close.iloc[-1],3))
            print("Drawn down 6m :", round(pourc_haut_6m*100,3),"%")

            if pourc_haut_6m < (seuil*coef) :
                print(f"\n🟢 🟢​ Achat 🟢 🟢​​")
            else :
                print(f"\n⏳ ⏳​ Attendre ⏳ ⏳​")

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
