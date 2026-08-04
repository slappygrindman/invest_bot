import os
import json
import requests
import yfinance as yf
import numpy as np
import io
import sys
import sqlite3
from datetime import date, datetime

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DB_FILE = "invest_bot.db"

SEUILS = {
    "T1": -0.05,
    "T2": -0.15,
    "T3": -0.21,
}

POIDS = {
    "T1": 0.20,
    "T2": 0.30,
    "T3": 0.50,
}

ETFS = [
    ("PAEEM.PA", "Amundi PEA Emerging Markets"),
    ("WPEA.PA", "Amundi PEA World"),
    ("CMSE.PA", "Amundi PEA S&P 500"),
]

# ============================================================
# BASE DE DONNÉES
# ============================================================
def get_conn():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS etfs (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            active      INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_closes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            date        TEXT NOT NULL,
            close       REAL NOT NULL,
            UNIQUE(ticker, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS buy_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            date            TEXT NOT NULL,
            seuil           TEXT NOT NULL,
            seuil_value     REAL,
            drawdown        REAL,
            poids           REAL,
            message         TEXT,
            executed        INTEGER DEFAULT 0,
            quantity        REAL,
            price           REAL,
            amount          REAL,
            executed_at     TEXT,
            note            TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS budget (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mois                TEXT NOT NULL UNIQUE,
            montant_mensuel     REAL,
            note                TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    # État des seuils (remplace state.json)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seuil_state (
            ticker      TEXT NOT NULL,
            seuil       TEXT NOT NULL,
            declenche   INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, seuil)
        )
    """)

    # Offset Telegram pour ne pas retraiter les mêmes messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS telegram_offset (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            last_update INTEGER DEFAULT 0
        )
    """)
    c.execute("INSERT OR IGNORE INTO telegram_offset (id, last_update) VALUES (1, 0)")

    # Insérer les ETF
    c.executemany(
        "INSERT OR IGNORE INTO etfs (ticker, name) VALUES (?, ?)",
        ETFS
    )

    # Initialiser les seuils pour chaque ETF
    for ticker, _ in ETFS:
        for label in SEUILS:
            c.execute(
                "INSERT OR IGNORE INTO seuil_state (ticker, seuil, declenche) VALUES (?, ?, 0)",
                (ticker, label)
            )

    conn.commit()
    conn.close()

# ============================================================
# TELEGRAM
# ============================================================
def envoyer_telegram(message, chat_id=None):
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN manquant")
        return False

    target = chat_id or CHAT_ID
    if not target:
        print("❌ CHAT_ID manquant")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        result = r.json()
        if result.get("ok"):
            return True
        print(f"❌ Erreur Telegram: {result.get('description')}")
        return False
    except Exception as e:
        print(f"❌ Exception Telegram: {e}")
        return False

def get_last_update_id():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT last_update FROM telegram_offset WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_last_update_id(update_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE telegram_offset SET last_update = ? WHERE id = 1", (update_id,))
    conn.commit()
    conn.close()

def process_telegram_commands():
    """Récupère et traite les commandes reçues depuis le dernier run."""
    if not BOT_TOKEN:
        return

    last_id = get_last_update_id()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_id + 1, "timeout": 0}

    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("ok"):
            print("Erreur getUpdates:", data)
            return

        updates = data.get("result", [])
        if not updates:
            print("Aucune nouvelle commande Telegram.")
            return

        max_id = last_id
        for upd in updates:
            max_id = max(max_id, upd["update_id"])
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue

            text = (msg.get("text") or "").strip()
            chat_id = str(msg["chat"]["id"])
            # On ne répond qu’au chat autorisé
            if CHAT_ID and chat_id != str(CHAT_ID):
                continue

            if not text.startswith("/"):
                continue

            print(f"Commande reçue: {text}")
            handle_command(text, chat_id)

        set_last_update_id(max_id)

    except Exception as e:
        print(f"Erreur process_telegram_commands: {e}")

def handle_command(text, chat_id):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]  # gère /budget@MonBot

    if cmd == "/help" or cmd == "/start":
        help_text = (
            "<b>Commandes disponibles</b>\n\n"
            "/budget → affiche le budget du mois\n"
            "/budget 500 → définit le budget mensuel à 500 € (remplace)\n"
            "/budget +200 → ajoute 200 € au budget du mois\n"
            "/budget -30 → retire 30 € au budget du mois\n"
            "/ok TICKER QTE → archive le dernier signal (ex: /ok PAEEM.PA 12.5)\n"
            "/ok TICKER QTE PRIX → idem + prix d’achat\n"
            "/signaux → 10 derniers signaux\n"
            "/signaux pending → seulement les non exécutés\n"
            "/historique → derniers closes\n"
            "/historique TICKER → closes d’un ETF\n"
            "/status → résumé global\n"
            "/help → cette aide"
        )
        envoyer_telegram(help_text, chat_id)

    elif cmd == "/budget":
        if len(parts) == 1:
            show_budget(chat_id)
        else:
            arg = parts[1].replace(",", ".")
            try:
                if arg[0] in ("+", "-"):
                    delta = float(arg)
                    adjust_budget(delta, chat_id)
                else:
                    montant = float(arg)
                    set_budget(montant, chat_id)
            except ValueError:
                envoyer_telegram("❌ Montant invalide. Exemple : /budget 500, /budget +200 ou /budget -30", chat_id)

    elif cmd == "/ok":
        if len(parts) < 3:
            envoyer_telegram("❌ Usage : /ok TICKER QUANTITÉ [PRIX]\nExemple : /ok PAEEM.PA 12.5 34.20", chat_id)
            return
        ticker = parts[1].upper()
        try:
            qty = float(parts[2].replace(",", "."))
            price = float(parts[3].replace(",", ".")) if len(parts) >= 4 else None
            mark_signal_executed(ticker, qty, price, chat_id)
        except ValueError:
            envoyer_telegram("❌ Quantité ou prix invalide.", chat_id)

    elif cmd == "/signaux":
        pending_only = len(parts) > 1 and parts[1].lower() == "pending"
        show_signaux(chat_id, pending_only)

    elif cmd == "/historique":
        ticker = parts[1].upper() if len(parts) > 1 else None
        show_historique(chat_id, ticker)

    elif cmd == "/status":
        show_status(chat_id)

    else:
        envoyer_telegram("Commande inconnue. Tape /help", chat_id)

# ============================================================
# COMMANDES MÉTIER
# ============================================================
def set_budget(montant, chat_id):
    mois = date.today().strftime("%Y-%m")

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO budget (mois, montant_mensuel)
        VALUES (?, ?)
        ON CONFLICT(mois) DO UPDATE SET
            montant_mensuel = excluded.montant_mensuel
    """, (mois, montant))
    conn.commit()
    conn.close()

    envoyer_telegram(
        f"✅ Budget du mois <b>{mois}</b> enregistré\n"
        f"Montant mensuel : <b>{montant:.2f} €</b>\n",
        chat_id
    )

def adjust_budget(delta, chat_id):
    """Ajoute (ou retire si delta est négatif) un montant au budget du mois en cours,
    au lieu de le remplacer. Ex : /budget +200 ou /budget -30."""
    mois = date.today().strftime("%Y-%m")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT montant_mensuel FROM budget WHERE mois = ?", (mois,))
    row = c.fetchone()
    montant_actuel = row[0] if row else 0.0

    nouveau_montant = montant_actuel + delta
    if nouveau_montant < 0:
        nouveau_montant = 0.0

    c.execute("""
        INSERT INTO budget (mois, montant_mensuel)
        VALUES (?, ?)
        ON CONFLICT(mois) DO UPDATE SET
            montant_mensuel = excluded.montant_mensuel
    """, (mois, nouveau_montant))
    conn.commit()
    conn.close()

    signe = "+" if delta >= 0 else ""
    envoyer_telegram(
        f"✅ Budget du mois <b>{mois}</b> ajusté ({signe}{delta:.2f} €)\n"
        f"Ancien montant : {montant_actuel:.2f} €\n"
        f"Nouveau montant mensuel : <b>{nouveau_montant:.2f} €</b>\n",
        chat_id
    )

def show_budget(chat_id):
    mois = date.today().strftime("%Y-%m")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT montant_mensuel FROM budget WHERE mois = ?", (mois,))
    row = c.fetchone()
    conn.close()

    if row:
        envoyer_telegram(
            f"📊 Budget {mois}\n"
            f"Montant mensuel : <b>{row[0]:.2f} €</b>\n",
            chat_id
        )
    else:
        envoyer_telegram(f"Aucun budget défini pour {mois}.\nUtilise /budget", chat_id)

def mark_signal_executed(ticker, quantity, price, chat_id):
    conn = get_conn()
    c = conn.cursor()

    # Dernier signal non exécuté pour ce ticker
    c.execute("""
        SELECT id, seuil, date, drawdown FROM buy_signals
        WHERE ticker = ? AND executed = 0
        ORDER BY id DESC LIMIT 1
    """, (ticker,))
    row = c.fetchone()

    if not row:
        conn.close()
        envoyer_telegram(f"❌ Aucun signal en attente pour <b>{ticker}</b>", chat_id)
        return

    signal_id, seuil, sig_date, drawdown = row
    amount = quantity * price if price is not None else None
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    c.execute("""
        UPDATE buy_signals SET
            executed = 1,
            quantity = ?,
            price = ?,
            amount = ?,
            executed_at = ?
        WHERE id = ?
    """, (quantity, price, amount, now, signal_id))
    conn.commit()
    conn.close()

    msg = (
        f"✅ Signal archivé\n"
        f"ETF : <b>{ticker}</b>\n"
        f"Seuil : {seuil}\n"
        f"Date signal : {sig_date}\n"
        f"Quantité : <b>{quantity}</b>\n"
    )
    if price is not None:
        msg += f"Prix : {price:.3f} €\nMontant : <b>{amount:.2f} €</b>\n"
    msg += f"Validé le : {now}"

    envoyer_telegram(msg, chat_id)

def show_signaux(chat_id, pending_only=False):
    conn = get_conn()
    c = conn.cursor()

    if pending_only:
        c.execute("""
            SELECT ticker, date, seuil, drawdown, poids
            FROM buy_signals WHERE executed = 0
            ORDER BY id DESC LIMIT 15
        """)
    else:
        c.execute("""
            SELECT ticker, date, seuil, drawdown, poids, executed, quantity, amount
            FROM buy_signals ORDER BY id DESC LIMIT 12
        """)

    rows = c.fetchall()
    conn.close()

    if not rows:
        envoyer_telegram("Aucun signal trouvé.", chat_id)
        return

    lines = ["<b>Signaux d’achat</b>\n"]
    for r in rows:
        if pending_only:
            ticker, d, seuil, dd, poids = r
            lines.append(f"🟡 {d} | {ticker} | {seuil} | DD {dd*100:.1f}% | {int(poids*100)}%")
        else:
            ticker, d, seuil, dd, poids, executed, qty, amount = r
            status = "✅" if executed else "🟡"
            extra = f" → {qty} u." if executed and qty else ""
            lines.append(f"{status} {d} | {ticker} | {seuil}{extra}")

    envoyer_telegram("\n".join(lines), chat_id)

def show_historique(chat_id, ticker=None):
    conn = get_conn()
    c = conn.cursor()

    if ticker:
        c.execute("""
            SELECT date, close FROM daily_closes
            WHERE ticker = ? ORDER BY date DESC LIMIT 10
        """, (ticker,))
        rows = c.fetchall()
        title = f"Historique {ticker}"
    else:
        c.execute("""
            SELECT ticker, date, close FROM daily_closes
            ORDER BY date DESC, ticker LIMIT 15
        """)
        rows = c.fetchall()
        title = "Derniers closes"

    conn.close()

    if not rows:
        envoyer_telegram("Aucune donnée de close.", chat_id)
        return

    lines = [f"<b>{title}</b>\n"]
    for r in rows:
        if ticker:
            lines.append(f"{r[0]} : {r[1]:.3f}")
        else:
            lines.append(f"{r[1]} | {r[0]} : {r[2]:.3f}")

    envoyer_telegram("\n".join(lines), chat_id)

def show_status(chat_id):
    mois = date.today().strftime("%Y-%m")
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT montant_mensuel FROM budget WHERE mois = ?", (mois,))
    bud = c.fetchone()

    c.execute("SELECT COUNT(*) FROM buy_signals WHERE executed = 0")
    pending = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM buy_signals WHERE executed = 1")
    done = c.fetchone()[0]

    conn.close()

    msg = f"<b>Status {mois}</b>\n\n"
    if bud:
        msg += f"Budget : <b>{bud[0]:.0f} €</b>\n\n"
    else:
        msg += "Budget : non défini\n\n"
    msg += f"Signaux en attente : <b>{pending}</b>\nSignaux exécutés : <b>{done}</b>"

    envoyer_telegram(msg, chat_id)

# ============================================================
# LOGIQUE MÉTIER (seuils + analyse)
# ============================================================
def get_seuil_state(ticker):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT seuil, declenche FROM seuil_state WHERE ticker = ?", (ticker,))
    rows = c.fetchall()
    conn.close()
    return {r[0]: bool(r[1]) for r in rows}

def set_seuil_declenche(ticker, seuil, value: bool):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE seuil_state SET declenche = ? WHERE ticker = ? AND seuil = ?",
        (1 if value else 0, ticker, seuil)
    )
    conn.commit()
    conn.close()

def verifier_seuils(ticker, pourc_haut_6m):
    """Retourne la liste des nouveaux seuils franchis."""
    state = get_seuil_state(ticker)
    nouveaux = []

    for label, seuil in SEUILS.items():
        deja = state.get(label, False)
        if pourc_haut_6m <= seuil:
            if not deja:
                nouveaux.append({
                    "label": label,
                    "seuil_ajuste": seuil,
                    "poids": POIDS[label],
                })
                set_seuil_declenche(ticker, label, True)
        else:
            # Réarmement
            if deja:
                set_seuil_declenche(ticker, label, False)

    return nouveaux

def save_daily_close(ticker, close_value):
    today = date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO daily_closes (ticker, date, close)
        VALUES (?, ?, ?)
    """, (ticker, today, float(close_value)))
    conn.commit()
    conn.close()

def save_buy_signal(ticker, seuil_label, seuil_value, drawdown, poids, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO buy_signals
        (ticker, date, seuil, seuil_value, drawdown, poids, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        date.today().isoformat(),
        seuil_label,
        seuil_value,
        drawdown,
        poids,
        message
    ))
    conn.commit()
    conn.close()

def get_investment_analysis():
    signaux = []
    output = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = output

    try:
        for ticker, _ in ETFS:
            df = yf.download(ticker, period="252d", progress=False)
            if df.empty:
                print(f"\n========== {ticker} ==========")
                print("Données indisponibles")
                continue

            close = df["Close"].squeeze()
            close_list = close.tolist()
            moit = int(len(close_list) / 2)

            log_square = []
            for i in range(1, len(close_list)):
                log_rdm_carre = (np.log(close_list[i] / close_list[i - 1])) ** 2
                log_square.append(log_rdm_carre)

            vola_6m = []
            for i in range(moit):
                vola = np.sqrt(np.sum(log_square[i:i + moit]) / moit) * np.sqrt(252)
                vola_6m.append(vola)

            coef = vola_6m[-1] / np.median(vola_6m) if vola_6m else 1.0
            max_6 = max(close[moit:])
            pourc_haut_6m = (close.iloc[-1] - max_6) / max_6

            # Sauvegarde du close du jour
            save_daily_close(ticker, close.iloc[-1])

            print(f"\n========== {ticker} ==========")
            print("Plus haut 6 mois :", round(max_6, 3))
            print("Prix :", round(close.iloc[-1], 3))
            print("Drawdown 6m :", round(pourc_haut_6m * 100, 1), "%")

            state = get_seuil_state(ticker)
            for label, seuil in SEUILS.items():
                statut = "Verrouillé" if state.get(label) else "Disponible"
                print(f"Seuil {label} ({seuil*100:.0f}%) : {statut}")

            nouveaux = verifier_seuils(ticker, pourc_haut_6m)
            for n in nouveaux:
                msg = (
                    f"🟢 SIGNAL D'ACHAT — {ticker}\n"
                    f"Seuil {n['label']} franchi ({n['seuil_ajuste']*100:.1f}%)\n"
                    f"Part enveloppe tactique : {int(n['poids']*100)}%\n"
                    f"Drawdown actuel : {pourc_haut_6m*100:.2f}%"
                )
                signaux.append(msg)
                save_buy_signal(
                    ticker, n["label"], n["seuil_ajuste"],
                    pourc_haut_6m, n["poids"], msg
                )
                print(f"\n{msg}")

    finally:
        sys.stdout = original_stdout

    return output.getvalue(), signaux

# ============================================================
# MAIN
# ============================================================
def main():
    print("=== Initialisation base de données ===")
    init_db()

    print("=== Traitement des commandes Telegram ===")
    process_telegram_commands()

    print("=== Analyse des ETF ===")
    analyse, signaux = get_investment_analysis()
    print(analyse)

    # Envoi de l’analyse
    chunks = [analyse[i:i + 4000] for i in range(0, len(analyse), 4000)]
    for chunk in chunks:
        envoyer_telegram(f"<pre>{chunk}</pre>")

    # Envoi des nouveaux signaux
    for signal in signaux:
        envoyer_telegram(signal)

    print("=== Terminé ===")

if __name__ == "__main__":
    main()
import os
import json
import requests
import yfinance as yf
import numpy as np
import io
import sys
import sqlite3
from datetime import date, datetime

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

DB_FILE = "invest_bot.db"

SEUILS = {
    "T1": -0.05,
    "T2": -0.15,
    "T3": -0.21,
}

POIDS = {
    "T1": 0.20,
    "T2": 0.30,
    "T3": 0.50,
}

ETFS = [
    ("PAEEM.PA", "Amundi PEA Emerging Markets"),
    ("WPEA.PA", "Amundi PEA World"),
    ("CMSE.PA", "Amundi PEA S&P 500"),
]

# ============================================================
# BASE DE DONNÉES
# ============================================================
def get_conn():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS etfs (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            active      INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_closes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            date        TEXT NOT NULL,
            close       REAL NOT NULL,
            UNIQUE(ticker, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS buy_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            date            TEXT NOT NULL,
            seuil           TEXT NOT NULL,
            seuil_value     REAL,
            drawdown        REAL,
            poids           REAL,
            message         TEXT,
            executed        INTEGER DEFAULT 0,
            quantity        REAL,
            price           REAL,
            amount          REAL,
            executed_at     TEXT,
            note            TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS budget (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            mois                TEXT NOT NULL UNIQUE,
            montant_mensuel     REAL,
            enveloppe_tactique  REAL,
            note                TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        )
    """)

    # État des seuils (remplace state.json)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seuil_state (
            ticker      TEXT NOT NULL,
            seuil       TEXT NOT NULL,
            declenche   INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, seuil)
        )
    """)

    # Offset Telegram pour ne pas retraiter les mêmes messages
    c.execute("""
        CREATE TABLE IF NOT EXISTS telegram_offset (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            last_update INTEGER DEFAULT 0
        )
    """)
    c.execute("INSERT OR IGNORE INTO telegram_offset (id, last_update) VALUES (1, 0)")

    # Insérer les ETF
    c.executemany(
        "INSERT OR IGNORE INTO etfs (ticker, name) VALUES (?, ?)",
        ETFS
    )

    # Initialiser les seuils pour chaque ETF
    for ticker, _ in ETFS:
        for label in SEUILS:
            c.execute(
                "INSERT OR IGNORE INTO seuil_state (ticker, seuil, declenche) VALUES (?, ?, 0)",
                (ticker, label)
            )

    conn.commit()
    conn.close()

# ============================================================
# TELEGRAM
# ============================================================
def envoyer_telegram(message, chat_id=None):
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN manquant")
        return False

    target = chat_id or CHAT_ID
    if not target:
        print("❌ CHAT_ID manquant")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        result = r.json()
        if result.get("ok"):
            return True
        print(f"❌ Erreur Telegram: {result.get('description')}")
        return False
    except Exception as e:
        print(f"❌ Exception Telegram: {e}")
        return False

def get_last_update_id():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT last_update FROM telegram_offset WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_last_update_id(update_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE telegram_offset SET last_update = ? WHERE id = 1", (update_id,))
    conn.commit()
    conn.close()

def process_telegram_commands():
    """Récupère et traite les commandes reçues depuis le dernier run."""
    if not BOT_TOKEN:
        return

    last_id = get_last_update_id()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_id + 1, "timeout": 0}

    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if not data.get("ok"):
            print("Erreur getUpdates:", data)
            return

        updates = data.get("result", [])
        if not updates:
            print("Aucune nouvelle commande Telegram.")
            return

        max_id = last_id
        for upd in updates:
            max_id = max(max_id, upd["update_id"])
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue

            text = (msg.get("text") or "").strip()
            chat_id = str(msg["chat"]["id"])
            # On ne répond qu’au chat autorisé
            if CHAT_ID and chat_id != str(CHAT_ID):
                continue

            if not text.startswith("/"):
                continue

            print(f"Commande reçue: {text}")
            handle_command(text, chat_id)

        set_last_update_id(max_id)

    except Exception as e:
        print(f"Erreur process_telegram_commands: {e}")

def handle_command(text, chat_id):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]  # gère /budget@MonBot

    if cmd == "/help" or cmd == "/start":
        help_text = (
            "<b>Commandes disponibles</b>\n\n"
            "/budget → affiche le budget du mois\n"
            "/budget 500 → définit le budget mensuel à 500 €\n"
            "/ok TICKER QTE → archive le dernier signal (ex: /ok PAEEM.PA 12.5)\n"
            "/ok TICKER QTE PRIX → idem + prix d’achat\n"
            "/signaux → 10 derniers signaux\n"
            "/signaux pending → seulement les non exécutés\n"
            "/historique → derniers closes\n"
            "/historique TICKER → closes d’un ETF\n"
            "/status → résumé global\n"
            "/help → cette aide"
        )
        envoyer_telegram(help_text, chat_id)

    elif cmd == "/budget":
        if len(parts) == 1:
            show_budget(chat_id)
        else:
            try:
                montant = float(parts[1].replace(",", "."))
                set_budget(montant, chat_id)
            except ValueError:
                envoyer_telegram("❌ Montant invalide. Exemple : /budget 500", chat_id)

    elif cmd == "/ok":
        if len(parts) < 3:
            envoyer_telegram("❌ Usage : /ok TICKER QUANTITÉ [PRIX]\nExemple : /ok PAEEM.PA 12.5 34.20", chat_id)
            return
        ticker = parts[1].upper()
        try:
            qty = float(parts[2].replace(",", "."))
            price = float(parts[3].replace(",", ".")) if len(parts) >= 4 else None
            mark_signal_executed(ticker, qty, price, chat_id)
        except ValueError:
            envoyer_telegram("❌ Quantité ou prix invalide.", chat_id)

    elif cmd == "/signaux":
        pending_only = len(parts) > 1 and parts[1].lower() == "pending"
        show_signaux(chat_id, pending_only)

    elif cmd == "/historique":
        ticker = parts[1].upper() if len(parts) > 1 else None
        show_historique(chat_id, ticker)

    elif cmd == "/status":
        show_status(chat_id)

    else:
        envoyer_telegram("Commande inconnue. Tape /help", chat_id)

# ============================================================
# COMMANDES MÉTIER
# ============================================================
def set_budget(montant, chat_id):
    mois = date.today().strftime("%Y-%m")
    enveloppe = montant * 0.20

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO budget (mois, montant_mensuel, enveloppe_tactique)
        VALUES (?, ?, ?)
        ON CONFLICT(mois) DO UPDATE SET
            montant_mensuel = excluded.montant_mensuel,
            enveloppe_tactique = excluded.enveloppe_tactique
    """, (mois, montant, enveloppe))
    conn.commit()
    conn.close()

    envoyer_telegram(
        f"✅ Budget du mois <b>{mois}</b> enregistré\n"
        f"Montant mensuel : <b>{montant:.2f} €</b>\n",
        chat_id
    )

def show_budget(chat_id):
    mois = date.today().strftime("%Y-%m")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT montant_mensuel, enveloppe_tactique FROM budget WHERE mois = ?", (mois,))
    row = c.fetchone()
    conn.close()

    if row:
        envoyer_telegram(
            f"📊 Budget {mois}\n"
            f"Montant mensuel : <b>{row[0]:.2f} €</b>\n",
            chat_id
        )
    else:
        envoyer_telegram(f"Aucun budget défini pour {mois}.\nUtilise /budget", chat_id)

def mark_signal_executed(ticker, quantity, price, chat_id):
    conn = get_conn()
    c = conn.cursor()

    # Dernier signal non exécuté pour ce ticker
    c.execute("""
        SELECT id, seuil, date, drawdown FROM buy_signals
        WHERE ticker = ? AND executed = 0
        ORDER BY id DESC LIMIT 1
    """, (ticker,))
    row = c.fetchone()

    if not row:
        conn.close()
        envoyer_telegram(f"❌ Aucun signal en attente pour <b>{ticker}</b>", chat_id)
        return

    signal_id, seuil, sig_date, drawdown = row
    amount = quantity * price if price is not None else None
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    c.execute("""
        UPDATE buy_signals SET
            executed = 1,
            quantity = ?,
            price = ?,
            amount = ?,
            executed_at = ?
        WHERE id = ?
    """, (quantity, price, amount, now, signal_id))
    conn.commit()
    conn.close()

    msg = (
        f"✅ Signal archivé\n"
        f"ETF : <b>{ticker}</b>\n"
        f"Seuil : {seuil}\n"
        f"Date signal : {sig_date}\n"
        f"Quantité : <b>{quantity}</b>\n"
    )
    if price is not None:
        msg += f"Prix : {price:.3f} €\nMontant : <b>{amount:.2f} €</b>\n"
    msg += f"Validé le : {now}"

    envoyer_telegram(msg, chat_id)

def show_signaux(chat_id, pending_only=False):
    conn = get_conn()
    c = conn.cursor()

    if pending_only:
        c.execute("""
            SELECT ticker, date, seuil, drawdown, poids
            FROM buy_signals WHERE executed = 0
            ORDER BY id DESC LIMIT 15
        """)
    else:
        c.execute("""
            SELECT ticker, date, seuil, drawdown, poids, executed, quantity, amount
            FROM buy_signals ORDER BY id DESC LIMIT 12
        """)

    rows = c.fetchall()
    conn.close()

    if not rows:
        envoyer_telegram("Aucun signal trouvé.", chat_id)
        return

    lines = ["<b>Signaux d’achat</b>\n"]
    for r in rows:
        if pending_only:
            ticker, d, seuil, dd, poids = r
            lines.append(f"🟡 {d} | {ticker} | {seuil} | DD {dd*100:.1f}% | {int(poids*100)}%")
        else:
            ticker, d, seuil, dd, poids, executed, qty, amount = r
            status = "✅" if executed else "🟡"
            extra = f" → {qty} u." if executed and qty else ""
            lines.append(f"{status} {d} | {ticker} | {seuil}{extra}")

    envoyer_telegram("\n".join(lines), chat_id)

def show_historique(chat_id, ticker=None):
    conn = get_conn()
    c = conn.cursor()

    if ticker:
        c.execute("""
            SELECT date, close FROM daily_closes
            WHERE ticker = ? ORDER BY date DESC LIMIT 10
        """, (ticker,))
        rows = c.fetchall()
        title = f"Historique {ticker}"
    else:
        c.execute("""
            SELECT ticker, date, close FROM daily_closes
            ORDER BY date DESC, ticker LIMIT 15
        """)
        rows = c.fetchall()
        title = "Derniers closes"

    conn.close()

    if not rows:
        envoyer_telegram("Aucune donnée de close.", chat_id)
        return

    lines = [f"<b>{title}</b>\n"]
    for r in rows:
        if ticker:
            lines.append(f"{r[0]} : {r[1]:.3f}")
        else:
            lines.append(f"{r[1]} | {r[0]} : {r[2]:.3f}")

    envoyer_telegram("\n".join(lines), chat_id)

def show_status(chat_id):
    mois = date.today().strftime("%Y-%m")
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT montant_mensuel, enveloppe_tactique FROM budget WHERE mois = ?", (mois,))
    bud = c.fetchone()

    c.execute("SELECT COUNT(*) FROM buy_signals WHERE executed = 0")
    pending = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM buy_signals WHERE executed = 1")
    done = c.fetchone()[0]

    conn.close()

    msg = f"<b>Status {mois}</b>\n\n"
    if bud:
        msg += f"Budget : <b>{bud[0]:.0f} €</b>\nEnveloppe tactique : <b>{bud[1]:.0f} €</b>\n\n"
    else:
        msg += "Budget : non défini\n\n"
    msg += f"Signaux en attente : <b>{pending}</b>\nSignaux exécutés : <b>{done}</b>"

    envoyer_telegram(msg, chat_id)

# ============================================================
# LOGIQUE MÉTIER (seuils + analyse)
# ============================================================
def get_seuil_state(ticker):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT seuil, declenche FROM seuil_state WHERE ticker = ?", (ticker,))
    rows = c.fetchall()
    conn.close()
    return {r[0]: bool(r[1]) for r in rows}

def set_seuil_declenche(ticker, seuil, value: bool):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE seuil_state SET declenche = ? WHERE ticker = ? AND seuil = ?",
        (1 if value else 0, ticker, seuil)
    )
    conn.commit()
    conn.close()

def verifier_seuils(ticker, pourc_haut_6m):
    """Retourne la liste des nouveaux seuils franchis."""
    state = get_seuil_state(ticker)
    nouveaux = []

    for label, seuil in SEUILS.items():
        deja = state.get(label, False)
        if pourc_haut_6m <= seuil:
            if not deja:
                nouveaux.append({
                    "label": label,
                    "seuil_ajuste": seuil,
                    "poids": POIDS[label],
                })
                set_seuil_declenche(ticker, label, True)
        else:
            # Réarmement
            if deja:
                set_seuil_declenche(ticker, label, False)

    return nouveaux

def save_daily_close(ticker, close_value):
    today = date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO daily_closes (ticker, date, close)
        VALUES (?, ?, ?)
    """, (ticker, today, float(close_value)))
    conn.commit()
    conn.close()

def save_buy_signal(ticker, seuil_label, seuil_value, drawdown, poids, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO buy_signals
        (ticker, date, seuil, seuil_value, drawdown, poids, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        date.today().isoformat(),
        seuil_label,
        seuil_value,
        drawdown,
        poids,
        message
    ))
    conn.commit()
    conn.close()

def get_investment_analysis():
    signaux = []
    output = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = output

    try:
        for ticker, _ in ETFS:
            df = yf.download(ticker, period="252d", progress=False)
            if df.empty:
                print(f"\n========== {ticker} ==========")
                print("Données indisponibles")
                continue

            close = df["Close"].squeeze()
            close_list = close.tolist()
            moit = int(len(close_list) / 2)

            log_square = []
            for i in range(1, len(close_list)):
                log_rdm_carre = (np.log(close_list[i] / close_list[i - 1])) ** 2
                log_square.append(log_rdm_carre)

            vola_6m = []
            for i in range(moit):
                vola = np.sqrt(np.sum(log_square[i:i + moit]) / moit) * np.sqrt(252)
                vola_6m.append(vola)

            coef = vola_6m[-1] / np.median(vola_6m) if vola_6m else 1.0
            max_6 = max(close[moit:])
            pourc_haut_6m = (close.iloc[-1] - max_6) / max_6

            # Sauvegarde du close du jour
            save_daily_close(ticker, close.iloc[-1])

            print(f"\n========== {ticker} ==========")
            print("Plus haut 6 mois :", round(max_6, 3))
            print("Prix :", round(close.iloc[-1], 3))
            print("Drawdown 6m :", round(pourc_haut_6m * 100, 1), "%")

            state = get_seuil_state(ticker)
            for label, seuil in SEUILS.items():
                statut = "Verrouillé" if state.get(label) else "Disponible"
                print(f"Seuil {label} ({seuil*100:.0f}%) : {statut}")

            nouveaux = verifier_seuils(ticker, pourc_haut_6m)
            for n in nouveaux:
                msg = (
                    f"🟢 SIGNAL D'ACHAT — {ticker}\n"
                    f"Seuil {n['label']} franchi ({n['seuil_ajuste']*100:.1f}%)\n"
                    f"Part enveloppe tactique : {int(n['poids']*100)}%\n"
                    f"Drawdown actuel : {pourc_haut_6m*100:.2f}%"
                )
                signaux.append(msg)
                save_buy_signal(
                    ticker, n["label"], n["seuil_ajuste"],
                    pourc_haut_6m, n["poids"], msg
                )
                print(f"\n{msg}")

    finally:
        sys.stdout = original_stdout

    return output.getvalue(), signaux

# ============================================================
# MAIN
# ============================================================
def main():
    print("=== Initialisation base de données ===")
    init_db()

    print("=== Traitement des commandes Telegram ===")
    process_telegram_commands()

    print("=== Analyse des ETF ===")
    analyse, signaux = get_investment_analysis()
    print(analyse)

    # Envoi de l’analyse
    chunks = [analyse[i:i + 4000] for i in range(0, len(analyse), 4000)]
    for chunk in chunks:
        envoyer_telegram(f"<pre>{chunk}</pre>")

    # Envoi des nouveaux signaux
    for signal in signaux:
        envoyer_telegram(signal)

    print("=== Terminé ===")

if __name__ == "__main__":
    main()
