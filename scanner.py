import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ============================================================
# SETTINGS
# ============================================================

MIN_MC = 5_000
MAX_MC = 100_000
MIN_LIQUIDITY = 5_000

SOLANA_RPC = "https://api.mainnet-beta.solana.com"

DEXSCREENER_PROFILES = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)

RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

seen = set()


# ============================================================
# HTTP HELPERS
# ============================================================

def get_json(url, timeout=20):
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 SolanaScanner/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as e:
        print(f"Request failed: {url}")
        print(f"Error: {e}")
        return None


def post_json(url, payload, timeout=20):
    try:
        body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": "Mozilla/5.0 SolanaScanner/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as e:
        print(f"POST failed: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    try:
        url = (
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
            "/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        }

        result = post_json(url, payload)

        if result and result.get("ok"):
            print("Telegram alert sent.")
            return True

        print("Telegram failed:", result)
        return False

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# DEXSCREENER
# ============================================================

def get_latest_solana_tokens():
    data = get_json(DEXSCREENER_PROFILES)

    if not data:
        return []

    if isinstance(data, list):
        profiles = data
    elif isinstance(data, dict):
        profiles = data.get("tokens", data.get("profiles", []))
    else:
        profiles = []

    results = []

    for token in profiles:
        try:
            if token.get("chainId") == "solana":
                address = (
                    token.get("tokenAddress")
                    or token.get("address")
                    or token.get("token")
                )

                if address:
                    results.append(address)

        except Exception:
            continue

    return results


def get_token_pairs(mint_address):
    url = (
        "https://api.dexscreener.com/latest/dex/tokens/"
        + urllib.parse.quote(mint_address)
    )

    data = get_json(url)

    if not data:
        return []

    pairs = data.get("pairs", [])

    if not isinstance(pairs, list):
        return []

    return [
        p for p in pairs
        if p.get("chainId") == "solana"
    ]


def choose_best_pair(pairs):
    if not pairs:
        return None

    valid_pairs = []

    for pair in pairs:
        try:
            liquidity = pair.get("liquidity", {}).get("usd") or 0
            valid_pairs.append((float(liquidity), pair))
        except Exception:
            continue

    if not valid_pairs:
        return None

    valid_pairs.sort(key=lambda x: x[0], reverse=True)

    return valid_pairs[0][1]


# ============================================================
# RUGCHECK
# ============================================================

def get_rugcheck_report(mint_address):
    url = (
        f"{RUGCHECK_API}/"
        f"{urllib.parse.quote(mint_address)}"
        "/report"
    )

    return get_json(url, timeout=25)


def parse_rugcheck(report):
    data = {
        "holders": "N/A",
        "mint": "Unknown",
        "freeze": "Unknown",
        "top10": "N/A",
        "risk_score": "N/A",
        "risk_level": "N/A",
    }

    if not isinstance(report, dict):
        return data

    # Holder count
    holders = report.get("totalHolders")

    if holders is not None:
        try:
            data["holders"] = int(holders)
        except Exception:
            data["holders"] = str(holders)

    # Authorities
    token = report.get("token", {})

    if isinstance(token, dict):
        mint_authority = token.get("mintAuthority")
        freeze_authority = token.get("freezeAuthority")

        data["mint"] = (
            "Revoked"
            if not mint_authority
            else "Active"
        )

        data["freeze"] = (
            "Revoked"
            if not freeze_authority
            else "Active"
        )

    # Top-10 concentration
    top_holders = report.get("topHolders", [])

    if isinstance(top_holders, list):
        percentages = []

        for holder in top_holders[:10]:
            if not isinstance(holder, dict):
                continue

            pct = holder.get("pct")

            if pct is None:
                pct = holder.get("percentage")

            try:
                percentages.append(float(pct))
            except Exception:
                pass

        if percentages:
            data["top10"] = sum(percentages)

    # RugCheck risk score
    score = report.get("score_normalised")

    if score is None:
        score = report.get("score")

    if score is not None:
        data["risk_score"] = score

    # Try to determine risk level from risks
    risks = report.get("risks", [])

    if isinstance(risks, list):
        levels = []

        for risk in risks:
            if isinstance(risk, dict):
                level = risk.get("level")

                if level:
                    levels.append(str(level).lower())

        if "danger" in levels:
            data["risk_level"] = "Danger"
        elif "warn" in levels:
            data["risk_level"] = "Warning"
        elif levels:
            data["risk_level"] = levels[0].title()

    return data


# ============================================================
# TOKEN SECURITY BACKUP
# ============================================================

def get_token_security(mint_address):
    """
    Backup authority check directly from Solana RPC.

    RugCheck is preferred because it also supplies holder
    and concentration information.
    """

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            mint_address,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
            },
        ],
    }

    result = post_json(SOLANA_RPC, payload)

    if not result:
        return "Unknown", "Unknown"

    try:
        value = result["result"]["value"]

        if not value:
            return "Unknown", "Unknown"

        parsed = value["data"]["parsed"]["info"]

        mint_authority = parsed.get("mintAuthority")
        freeze_authority = parsed.get("freezeAuthority")

        mint_status = (
            "Revoked"
            if not mint_authority
            else "Active"
        )

        freeze_status = (
            "Revoked"
            if not freeze_authority
            else "Active"
        )

        return mint_status, freeze_status

    except Exception as e:
        print("Security parsing error:", e)
        return "Unknown", "Unknown"


# ============================================================
# AGE
# ============================================================

def format_age(pair):
    created = pair.get("pairCreatedAt")

    if not created:
        return "N/A"

    try:
        created_seconds = float(created) / 1000
        now = time.time()

        age_seconds = max(0, now - created_seconds)

        minutes = int(age_seconds / 60)

        if minutes < 60:
            return f"{minutes} min"

        hours = minutes // 60
        remaining_minutes = minutes % 60

        if hours < 24:
            return f"{hours}h {remaining_minutes}m"

        days = hours // 24

        return f"{days}d"

    except Exception:
        return "N/A"


# ============================================================
# FORMATTING
# ============================================================

def money(value):
    try:
        value = float(value)

        if value >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"

        if value >= 1_000:
            return f"${value:,.0f}"

        return f"${value:.2f}"

    except Exception:
        return "N/A"


def format_holders(value):
    if value == "N/A":
        return "N/A"

    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def format_percent(value):
    if value == "N/A":
        return "N/A"

    try:
        return f"{float(value):.1f}%"
    except Exception:
        return str(value)


# ============================================================
# TOKEN ANALYSIS
# ============================================================

def analyse_token(mint_address):
    print(f"Checking: {mint_address}")

    pairs = get_token_pairs(mint_address)

    if not pairs:
        return None

    pair = choose_best_pair(pairs)

    if not pair:
        return None

    try:
        liquidity = float(
            pair.get("liquidity", {}).get("usd") or 0
        )

        market_cap = float(
            pair.get("marketCap")
            or pair.get("fdv")
            or 0
        )

        volume = float(
            pair.get("volume", {}).get("h24") or 0
        )

    except Exception:
        return None

    # Apply the scanner filters
    if market_cap < MIN_MC:
        return None

    if market_cap > MAX_MC:
        return None

    if liquidity < MIN_LIQUIDITY:
        return None

    base_token = pair.get("baseToken", {})

    name = (
        base_token.get("name")
        or "Unknown"
    )

    symbol = (
        base_token.get("symbol")
        or "UNKNOWN"
    )

    # RugCheck
    report = get_rugcheck_report(mint_address)

    security = parse_rugcheck(report)

    # Backup security check if RugCheck didn't provide it
    if security["mint"] == "Unknown" or security["freeze"] == "Unknown":
        mint_status, freeze_status = get_token_security(
            mint_address
        )

        if security["mint"] == "Unknown":
            security["mint"] = mint_status

        if security["freeze"] == "Unknown":
            security["freeze"] = freeze_status

    age = format_age(pair)

    dex_url = (
        f"https://dexscreener.com/solana/"
        f"{mint_address}"
    )

    rug_url = (
        f"https://rugcheck.xyz/tokens/"
        f"{mint_address}"
    )

    return {
        "name": name,
        "symbol": symbol,
        "mint": mint_address,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume": volume,
        "holders": security["holders"],
        "age": age,
        "mint_authority": security["mint"],
        "freeze_authority": security["freeze"],
        "top10": security["top10"],
        "risk_score": security["risk_score"],
        "risk_level": security["risk_level"],
        "dex_url": dex_url,
        "rug_url": rug_url,
    }


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_alert(token):
    return (
        "🚨 NEW SOLANA TOKEN\n\n"

        f"Name: {token['name']} "
        f"({token['symbol']})\n"
        f"CA: {token['mint']}\n\n"

        f"💰 MC: {money(token['market_cap'])}\n"
        f"💧 Liquidity: {money(token['liquidity'])}\n"
        f"👥 Holders: {format_holders(token['holders'])}\n"
        f"⏱ Age: {token['age']}\n"
        f"📊 Volume: {money(token['volume'])}\n\n"

        f"🔒 Mint: {token['mint_authority']}\n"
        f"❄️ Freeze: {token['freeze_authority']}\n"
        f"🐋 Top 10: {format_percent(token['top10'])}\n"

        f"🛡 RugCheck: {token['risk_level']} "
        f"({token['risk_score']})\n\n"

        f"🔗 DexScreener: {token['dex_url']}\n"
        f"🔗 RugCheck: {token['rug_url']}\n\n"

        "⚠️ DYOR. This alert is data only, "
        "not financial advice."
    )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():
    print("=" * 60)
    print("SOLANA SCANNER STARTED")
    print(
        f"Filters: MC ${MIN_MC:,}-${MAX_MC:,} | "
        f"Liquidity ${MIN_LIQUIDITY:,}+"
    )
    print(
        datetime.now(timezone.utc).strftime(
            "UTC time: %Y-%m-%d %H:%M:%S"
        )
    )
    print("=" * 60)

    if not TELEGRAM_BOT_TOKEN:
        print("WARNING: TELEGRAM_BOT_TOKEN is missing.")

    if not TELEGRAM_CHAT_ID:
        print("WARNING: TELEGRAM_CHAT_ID is missing.")

    addresses = get_latest_solana_tokens()

    print(f"Found {len(addresses)} latest Solana token profiles.")

    if not addresses:
        print("No token profiles returned.")
        return

    # Avoid scanning too many tokens in one GitHub Actions run
    addresses = addresses[:50]

    for mint_address in addresses:

        if mint_address in seen:
            continue

        try:
            token = analyse_token(mint_address)

            if not token:
                continue

            print(
                f"MATCH: {token['name']} | "
                f"MC {money(token['market_cap'])} | "
                f"Liquidity {money(token['liquidity'])}"
            )

            message = build_alert(token)

            if send_telegram(message):
                seen.add(mint_address)

            # Small delay to reduce API pressure
            time.sleep(1)

        except Exception as e:
            print(
                f"ERROR scanning {mint_address}: {e}"
            )
            continue

    print("=" * 60)
    print("SCAN FINISHED")
    print("=" * 60)


if __name__ == "__main__":
    main()

This version uses RugCheck's documented token report for total holders, top holders, authorities and risk information, rather than trying to calculate holder counts from only Solana's 20-largest-account RPC response.

Important: don't put your Telegram bot token inside this code. We'll add it securely through GitHub Actions Secrets in the next step.
