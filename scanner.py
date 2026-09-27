import os
import time
import requests
from datetime import datetime, timezone

# ============================================================
# SETTINGS
# ============================================================

MIN_MC = 5_000
MAX_MC = 100_000
MIN_LIQUIDITY = 5_000

SCAN_INTERVAL = 60

SOLANA_RPC = "https://api.mainnet-beta.solana.com"

DEX_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

DEX_BOOSTS_URL = (
    "https://api.dexscreener.com/token-boosts/latest/v1"
)

DEX_TOKEN_URL = (
    "https://api.dexscreener.com/tokens/v1/solana/"
)

RUGCHECK_URL = (
    "https://api.rugcheck.xyz/v1/tokens/{}/report"
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Tokens already sent to Telegram
seen = set()

# Reusable HTTP session
session = requests.Session()

session.headers.update({
    "User-Agent": "Solana-Token-Scanner/1.0",
    "Accept": "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def money(value):
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value:,.0f}"

    return f"${value:.2f}"


def shorten_address(address):
    if not address:
        return "Unknown"

    if len(address) <= 12:
        return address

    return address[:6] + "..." + address[-6:]


def format_age(created_at):
    if not created_at:
        return "Unknown"

    try:
        created_at = float(created_at)

        # DexScreener gives milliseconds
        if created_at > 10_000_000_000:
            created_at /= 1000

        now = time.time()
        seconds = max(0, now - created_at)

        minutes = int(seconds / 60)

        if minutes < 60:
            return f"{minutes} min"

        hours = minutes // 60

        if hours < 24:
            return f"{hours} hr"

        days = hours // 24

        return f"{days} day"

    except Exception:
        return "Unknown"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
        print("Required:")
        print("TELEGRAM_BOT_TOKEN")
        print("TELEGRAM_CHAT_ID")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=20
        )

        if response.ok:
            print("Telegram alert sent.")
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("Telegram connection error:", e)

    return False


# ============================================================
# DEXSCREENER
# ============================================================

def get_latest_profiles():
    try:
        response = session.get(
            DEX_PROFILES_URL,
            timeout=20
        )

        if not response.ok:
            print(
                "DexScreener profiles error:",
                response.status_code
            )
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print("DexScreener profiles error:", e)
        return []


def get_latest_boosts():
    try:
        response = session.get(
            DEX_BOOSTS_URL,
            timeout=20
        )

        if not response.ok:
            print(
                "DexScreener boosts error:",
                response.status_code
            )
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print("DexScreener boosts error:", e)
        return []


def get_token_pairs(mint):
    try:
        url = DEX_TOKEN_URL + mint

        response = session.get(
            url,
            timeout=20
        )

        if not response.ok:
            print(
                "DexScreener token error:",
                response.status_code,
                mint
            )
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print(
            "DexScreener token request error:",
            mint,
            e
        )
        return []


def choose_best_pair(pairs, mint):
    solana_pairs = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != "solana":
            continue

        base = pair.get("baseToken") or {}

        if base.get("address") != mint:
            continue

        liquidity = pair.get("liquidity") or {}

        liquidity_usd = safe_float(
            liquidity.get("usd")
        )

        solana_pairs.append(
            (liquidity_usd, pair)
        )

    if not solana_pairs:
        return None

    # Highest-liquidity pair
    solana_pairs.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return solana_pairs[0][1]


# ============================================================
# RUGCHECK
# ============================================================

def get_rugcheck_report(mint):
    url = RUGCHECK_URL.format(mint)

    try:
        response = session.get(
            url,
            timeout=25
        )

        if not response.ok:
            print(
                "RugCheck error:",
                response.status_code,
                mint
            )
            return {}

        data = response.json()

        if isinstance(data, dict):
            return data

        return {}

    except Exception as e:
        print(
            "RugCheck request error:",
            mint,
            e
        )
        return {}


# ============================================================
# SECURITY DATA
# ============================================================

def get_security_data(mint):
    report = get_rugcheck_report(mint)

    if not report:
        return {
            "mint_authority": "Unknown",
            "freeze_authority": "Unknown",
            "holders": "Unknown",
            "top10": "Unknown",
            "risk": "Unavailable",
        }

    token_data = report.get("token") or {}

    # -------------------------
    # Mint authority
    # -------------------------

    mint_authority = token_data.get(
        "mintAuthority"
    )

    if mint_authority:
        mint_status = "Active"
    else:
        mint_status = "Revoked"

    # -------------------------
    # Freeze authority
    # -------------------------

    freeze_authority = token_data.get(
        "freezeAuthority"
    )

    if freeze_authority:
        freeze_status = "Active"
    else:
        freeze_status = "Revoked"

    # -------------------------
    # Holder count
    # -------------------------

    holders = (
        report.get("totalHolders")
        or report.get("holderCount")
        or report.get("holdersCount")
        or report.get("holders")
    )

    if isinstance(holders, list):
        holders = len(holders)

    if holders is not None:
        holders = safe_int(
            holders,
            default=0
        )

    if not holders:
        holders_display = "Unknown"
    else:
        holders_display = f"{holders:,}"

    # -------------------------
    # Top 10 concentration
    # -------------------------

    top_holders = report.get(
        "topHolders"
    )

    top10 = None

    if isinstance(top_holders, list):
        percentages = []

        for holder in top_holders[:10]:
            if not isinstance(holder, dict):
                continue

            pct = holder.get("pct")

            if pct is not None:
                percentages.append(
                    safe_float(pct)
                )

        if percentages:
            top10 = sum(percentages)

    if top10 is None:
        top10_display = "Unknown"
    else:
        top10_display = f"{top10:.1f}%"

    # -------------------------
    # Risk information
    # -------------------------

    risks = report.get("risks")

    risk_names = []

    if isinstance(risks, list):
        for risk in risks[:3]:
            if not isinstance(risk, dict):
                continue

            name = risk.get("name")

            if name:
                risk_names.append(str(name))

    if risk_names:
        risk_display = ", ".join(risk_names)
    else:
        risk_display = "No reported risks"

    return {
        "mint_authority": mint_status,
        "freeze_authority": freeze_status,
        "holders": holders_display,
        "top10": top10_display,
        "risk": risk_display,
    }


# ============================================================
# BUILD ALERT
# ============================================================

def build_alert(pair, security):
    base = pair.get("baseToken") or {}

    mint = base.get("address", "Unknown")

    name = base.get("name") or "Unknown"

    symbol = base.get("symbol") or ""

    if symbol:
        token_name = f"{name} (${symbol})"
    else:
        token_name = name

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv")
        )

    liquidity_data = pair.get(
        "liquidity"
    ) or {}

    liquidity = safe_float(
        liquidity_data.get("usd")
    )

    volume_data = pair.get(
        "volume"
    ) or {}

    volume_24h = safe_float(
        volume_data.get("h24")
    )

    age = format_age(
        pair.get("pairCreatedAt")
    )

    dex_url = pair.get("url")

    if not dex_url:
        dex_url = (
            "https://dexscreener.com/solana/"
            + mint
        )

    rug_url = (
        "https://rugcheck.xyz/tokens/"
        + mint
    )

    alert = f"""🚨 NEW SOLANA TOKEN

Name: {token_name}
CA: {mint}

💰 MC: {money(market_cap)}
💧 Liquidity: {money(liquidity)}
👥 Holders: {security["holders"]}
⏱ Age: {age}
📊 Volume 24h: {money(volume_24h)}

🔒 Mint: {security["mint_authority"]}
❄️ Freeze: {security["freeze_authority"]}
🐋 Top 10: {security["top10"]}

⚠️ RugCheck:
{security["risk"]}

🔗 DexScreener:
{dex_url}

🔗 RugCheck:
{rug_url}

⚠️ DYOR — This alert is only a screening tool.
Do not buy a token solely because it passed these filters.
"""

    return alert


# ============================================================
# PROCESS TOKEN
# ============================================================

def process_token(mint):
    if not mint:
        return

    if mint in seen:
        return

    print()
    print("=" * 60)
    print("Checking:", mint)

    pairs = get_token_pairs(mint)

    if not pairs:
        print("No DexScreener pair.")
        return

    pair = choose_best_pair(
        pairs,
        mint
    )

    if not pair:
        print("No Solana pair found.")
        return

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv")
        )

    liquidity_data = pair.get(
        "liquidity"
    ) or {}

    liquidity = safe_float(
        liquidity_data.get("usd")
    )

    print(
        "MC:",
        money(market_cap),
        "| Liquidity:",
        money(liquidity)
    )

    # -------------------------
    # Market cap filter
    # -------------------------

    if market_cap < MIN_MC:
        print("Rejected: MC too low.")
        return

    if market_cap > MAX_MC:
        print("Rejected: MC too high.")
        return

    # -------------------------
    # Liquidity filter
    # -------------------------

    if liquidity < MIN_LIQUIDITY:
        print("Rejected: liquidity too low.")
        return

    print("PASSED MC + LIQUIDITY FILTER")

    # -------------------------
    # Security
    # -------------------------

    security = get_security_data(
        mint
    )

    # -------------------------
    # Alert
    # -------------------------

    message = build_alert(
        pair,
        security
    )

    success = send_telegram(
        message
    )

    if success:
        seen.add(mint)
        print(
            "Token added to seen list:",
            mint
        )


# ============================================================
# DISCOVER TOKENS
# ============================================================

def discover_tokens():
    addresses = set()

    # Latest token profiles
    profiles = get_latest_profiles()

    for item in profiles:
        if not isinstance(item, dict):
            continue

        if item.get("chainId") != "solana":
            continue

        mint = item.get("tokenAddress")

        if mint:
            addresses.add(mint)

    # Latest boosts
    boosts = get_latest_boosts()

    for item in boosts:
        if not isinstance(item, dict):
            continue

        if item.get("chainId") != "solana":
            continue

        mint = item.get("tokenAddress")

        if mint:
            addresses.add(mint)

    return list(addresses)


# ============================================================
# TEST CONFIGURATION
# ============================================================

def check_configuration():
    print("=" * 60)
    print("SOLANA TELEGRAM SCANNER")
    print("=" * 60)

    print(
        "Minimum MC:",
        money(MIN_MC)
    )

    print(
        "Maximum MC:",
        money(MAX_MC)
    )

    print(
        "Minimum liquidity:",
        money(MIN_LIQUIDITY)
    )

    print(
        "Scan interval:",
        SCAN_INTERVAL,
        "seconds"
    )

    if not TELEGRAM_TOKEN:
        print(
            "❌ TELEGRAM_BOT_TOKEN is missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "❌ TELEGRAM_CHAT_ID is missing."
        )
        return False

    print("✅ Telegram token found.")
    print("✅ Telegram chat ID found.")

    return True


# ============================================================
# MAIN SCANNER
# ============================================================

def scan():
    print()
    print(
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    tokens = discover_tokens()

    print(
        "Discovered",
        len(tokens),
        "Solana token addresses."
    )

    if not tokens:
        print(
            "No tokens discovered this cycle."
        )
        return

    checked = 0

    for mint in tokens:
        try:
            process_token(mint)
            checked += 1

        except Exception as e:
            print(
                "Token processing error:",
                mint,
                e
            )

    print(
        "Finished cycle.",
        "Checked:",
        checked,
        "| Alerts:",
        len(seen)
    )


def main():
    if not check_configuration():
        print()
        print(
            "Scanner stopped because configuration "
            "is incomplete."
        )
        return

    print()
    print("🚀 Scanner started.")
    print()

    while True:
        try:
            scan()

        except KeyboardInterrupt:
            print(
                "Scanner stopped."
            )
            break

        except Exception as e:
            print(
                "MAIN SCANNER ERROR:",
                e
            )

        print()
        print(
            "Waiting",
            SCAN_INTERVAL,
            "seconds..."
        )
        print()

        time.sleep(
            SCAN_INTERVAL
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
