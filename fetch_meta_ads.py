#!/usr/bin/env python3
"""
Sarkar Meta Ads Data Fetcher
------------------------------
Pulls campaign-level performance, ad-level data, and creative details from
the Meta Marketing API across BOTH Sarkar ad accounts, and stores it into a
local DuckDB database. Every row is tagged with which ad account it came
from, so you can build both a consolidated view and a per-account view from
the same tables.

Normal run (last 7 days, self-healing rolling window):
    python3 fetch_meta_ads.py

One-time backfill (e.g. 10 days, since the account was inactive before that):
    python3 fetch_meta_ads.py --days 10

Runs automatically at 8am via a scheduled job (see SETUP.md).
"""

import os
import sys
import json
import argparse
from datetime import datetime, date, timedelta

import requests
import duckdb
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()  # reads .env file in the same folder

ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
AD_ACCOUNT_IDS_RAW = os.environ.get("META_AD_ACCOUNT_IDS")
DB_PATH = os.environ.get(
    "SARKAR_DB_PATH", os.path.expanduser("~/sarkar reports/sarkar.duckdb")
)
API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

if not ACCESS_TOKEN or not AD_ACCOUNT_IDS_RAW:
    print("ERROR: Set META_ACCESS_TOKEN and META_AD_ACCOUNT_IDS in your .env file.")
    sys.exit(1)

AD_ACCOUNT_IDS = [a.strip() for a in AD_ACCOUNT_IDS_RAW.split(",") if a.strip()]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(path, params):
    """GET request against the Graph API with basic pagination + error handling."""
    url = f"{BASE_URL}/{path}"
    params = {**params, "access_token": ACCESS_TOKEN}
    all_data = []

    while url:
        resp = requests.get(url, params=params)
        payload = resp.json()

        if "error" in payload:
            err = payload["error"]
            print(f"API ERROR [{err.get('code')}]: {err.get('message')}")
            sys.exit(1)

        all_data.extend(payload.get("data", []))

        paging = payload.get("paging", {})
        next_url = paging.get("next")
        if next_url:
            url = next_url
            params = {}  # next_url already has all params baked in
        else:
            url = None

    return all_data


def fetch_campaign_insights(ad_account_id, lookback_days):
    """Daily campaign-level performance for one ad account."""
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    until = date.today().isoformat()

    fields = [
        "campaign_id", "campaign_name", "spend", "impressions", "clicks",
        "reach", "ctr", "cpc", "cpm", "actions", "action_values",
        "date_start", "date_stop",
    ]

    params = {
        "level": "campaign",
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,  # daily breakdown, not one aggregated blob
        "limit": 500,
    }

    rows = api_get(f"{ad_account_id}/insights", params)
    for r in rows:
        r["ad_account_id"] = ad_account_id
    return rows


def fetch_account_daily_insights(ad_account_id, lookback_days):
    """
    Account-level daily insights — used for the funnel table so spend/reach/
    impressions match a true account total (reach specifically can't be
    summed across campaigns without double-counting shared audiences).
    """
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    until = date.today().isoformat()

    fields = [
        "spend", "impressions", "reach", "clicks", "ctr",
        "actions", "action_values", "date_start", "date_stop",
    ]

    params = {
        "level": "account",
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
        "limit": 500,
    }

    rows = api_get(f"{ad_account_id}/insights", params)
    for r in rows:
        r["ad_account_id"] = ad_account_id
    return rows


def fetch_ad_level_data(ad_account_id, lookback_days):
    """Ad-level performance for one ad account."""
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    until = date.today().isoformat()

    fields = [
        "ad_id", "ad_name", "campaign_name", "adset_name", "spend",
        "impressions", "clicks", "ctr", "cpc", "actions",
        "date_start", "date_stop",
    ]

    params = {
        "level": "ad",
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
        "limit": 500,
    }

    rows = api_get(f"{ad_account_id}/insights", params)
    for r in rows:
        r["ad_account_id"] = ad_account_id
    return rows


def fetch_creatives(ad_account_id):
    """Creative details (image/video, copy, thumbnail) for one ad account."""
    fields = ["id", "name", "title", "body", "image_url", "thumbnail_url", "object_type"]
    params = {"fields": ",".join(fields), "limit": 500}

    rows = api_get(f"{ad_account_id}/adcreatives", params)
    for r in rows:
        r["ad_account_id"] = ad_account_id
    return rows


# ---------------------------------------------------------------------------
# Funnel parsing
# ---------------------------------------------------------------------------

# Meta reports the same funnel event under slightly different action_type
# names depending on pixel/CAPI setup. Each list is checked in order and the
# first match found is used, so more specific/standard names win.
FUNNEL_ACTION_TYPES = {
    "landing_page_views": ["landing_page_view"],
    "content_views": ["omni_view_content", "view_content"],
    "adds_to_cart": ["omni_add_to_cart", "add_to_cart"],
    "checkouts_initiated": ["omni_initiated_checkout", "initiate_checkout"],
    "payment_info_added": ["omni_add_payment_info", "add_payment_info"],
    "purchases": ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"],
}


def _extract_action_count(actions, type_candidates):
    """Find the first matching action_type in a Meta actions list and return its value."""
    if not actions:
        return 0
    by_type = {a.get("action_type"): a.get("value") for a in actions}
    for candidate in type_candidates:
        if candidate in by_type and by_type[candidate] is not None:
            try:
                return int(float(by_type[candidate]))
            except (ValueError, TypeError):
                return 0
    return 0


def _extract_purchase_value(action_values):
    """Revenue = the 'value' side of the purchase action, not the count."""
    if not action_values:
        return 0.0
    by_type = {a.get("action_type"): a.get("value") for a in action_values}
    for candidate in FUNNEL_ACTION_TYPES["purchases"]:
        if candidate in by_type and by_type[candidate] is not None:
            try:
                return float(by_type[candidate])
            except (ValueError, TypeError):
                return 0.0
    return 0.0


def build_daily_funnel(account_daily_rows):
    """
    Parse account-level daily rows into the named funnel columns matching
    the client sheet (Content Views, ATC, Checkout, Payment Info, Purchases,
    Revenue) — one row per (ad_account_id, date), no cross-campaign summing.
    """
    funnel_rows = []

    for r in account_daily_rows:
        actions = r.get("actions", [])
        action_values = r.get("action_values", [])

        row = {
            "ad_account_id": r.get("ad_account_id"),
            "date": r.get("date_start"),
            "spend": float(r.get("spend", 0) or 0),
            "impressions": int(r.get("impressions", 0) or 0),
            "reach": int(r.get("reach", 0) or 0),
            "clicks": int(r.get("clicks", 0) or 0),
            "revenue": _extract_purchase_value(action_values),
        }

        for col, candidates in FUNNEL_ACTION_TYPES.items():
            row[col] = _extract_action_count(actions, candidates)

        funnel_rows.append(row)

    return funnel_rows


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return duckdb.connect(DB_PATH)


def init_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_campaign_insights (
            ad_account_id VARCHAR,
            campaign_id VARCHAR,
            campaign_name VARCHAR,
            date_start DATE,
            date_stop DATE,
            spend DOUBLE,
            impressions BIGINT,
            clicks BIGINT,
            reach BIGINT,
            ctr DOUBLE,
            cpc DOUBLE,
            cpm DOUBLE,
            actions VARCHAR,
            action_values VARCHAR,
            fetched_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_ad_insights (
            ad_account_id VARCHAR,
            ad_id VARCHAR,
            ad_name VARCHAR,
            campaign_name VARCHAR,
            adset_name VARCHAR,
            date_start DATE,
            date_stop DATE,
            spend DOUBLE,
            impressions BIGINT,
            clicks BIGINT,
            ctr DOUBLE,
            cpc DOUBLE,
            actions VARCHAR,
            fetched_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_creatives (
            ad_account_id VARCHAR,
            creative_id VARCHAR,
            name VARCHAR,
            title VARCHAR,
            body VARCHAR,
            image_url VARCHAR,
            thumbnail_url VARCHAR,
            object_type VARCHAR,
            fetched_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_daily_funnel (
            ad_account_id VARCHAR,
            date DATE,
            spend DOUBLE,
            impressions BIGINT,
            reach BIGINT,
            clicks BIGINT,
            landing_page_views BIGINT,
            content_views BIGINT,
            adds_to_cart BIGINT,
            checkouts_initiated BIGINT,
            payment_info_added BIGINT,
            purchases BIGINT,
            revenue DOUBLE,
            fetched_at TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            fetched_at TIMESTAMP,
            ad_accounts VARCHAR,
            lookback_days INTEGER,
            rows_campaign INTEGER,
            rows_ad INTEGER,
            rows_creative INTEGER,
            rows_funnel INTEGER,
            status VARCHAR
        )
    """)


def upsert_campaign_insights(con, rows, fetched_at):
    for r in rows:
        con.execute("""
            DELETE FROM meta_campaign_insights
            WHERE ad_account_id = ? AND campaign_id = ? AND date_start = ?
        """, [r.get("ad_account_id"), r.get("campaign_id"), r.get("date_start")])

        con.execute("""
            INSERT INTO meta_campaign_insights VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            r.get("ad_account_id"),
            r.get("campaign_id"),
            r.get("campaign_name"),
            r.get("date_start"),
            r.get("date_stop"),
            float(r.get("spend", 0) or 0),
            int(r.get("impressions", 0) or 0),
            int(r.get("clicks", 0) or 0),
            int(r.get("reach", 0) or 0),
            float(r.get("ctr", 0) or 0),
            float(r.get("cpc", 0) or 0),
            float(r.get("cpm", 0) or 0),
            json.dumps(r.get("actions", [])),
            json.dumps(r.get("action_values", [])),
            fetched_at,
        ])


def migrate_existing_tables(con):
    """
    Your DB already has data from earlier runs, made before this funnel
    table existed. This adds the one new column fetch_log needs without
    touching any data you already fetched.
    """
    try:
        con.execute("ALTER TABLE fetch_log ADD COLUMN rows_funnel INTEGER")
    except Exception:
        pass  # column already exists — fine, nothing to do


def upsert_daily_funnel(con, rows, fetched_at):
    for r in rows:
        con.execute("""
            DELETE FROM meta_daily_funnel
            WHERE ad_account_id = ? AND date = ?
        """, [r["ad_account_id"], r["date"]])

        con.execute("""
            INSERT INTO meta_daily_funnel VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            r["ad_account_id"],
            r["date"],
            r["spend"],
            r["impressions"],
            r["reach"],
            r["clicks"],
            r["landing_page_views"],
            r["content_views"],
            r["adds_to_cart"],
            r["checkouts_initiated"],
            r["payment_info_added"],
            r["purchases"],
            r["revenue"],
            fetched_at,
        ])


def upsert_ad_insights(con, rows, fetched_at):
    for r in rows:
        con.execute("""
            DELETE FROM meta_ad_insights
            WHERE ad_account_id = ? AND ad_id = ? AND date_start = ?
        """, [r.get("ad_account_id"), r.get("ad_id"), r.get("date_start")])

        con.execute("""
            INSERT INTO meta_ad_insights VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            r.get("ad_account_id"),
            r.get("ad_id"),
            r.get("ad_name"),
            r.get("campaign_name"),
            r.get("adset_name"),
            r.get("date_start"),
            r.get("date_stop"),
            float(r.get("spend", 0) or 0),
            int(r.get("impressions", 0) or 0),
            int(r.get("clicks", 0) or 0),
            float(r.get("ctr", 0) or 0),
            float(r.get("cpc", 0) or 0),
            json.dumps(r.get("actions", [])),
            fetched_at,
        ])


def upsert_creatives(con, rows, fetched_at):
    for r in rows:
        con.execute("""
            DELETE FROM meta_creatives WHERE ad_account_id = ? AND creative_id = ?
        """, [r.get("ad_account_id"), r.get("id")])

        con.execute("""
            INSERT INTO meta_creatives VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            r.get("ad_account_id"),
            r.get("id"),
            r.get("name"),
            r.get("title"),
            r.get("body"),
            r.get("image_url"),
            r.get("thumbnail_url"),
            r.get("object_type"),
            fetched_at,
        ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch Sarkar Meta Ads data into DuckDB.")
    parser.add_argument(
        "--days", type=int, default=7,
        help="How many days back to pull (default 7). Use a larger number for a one-time backfill, e.g. --days 10."
    )
    args = parser.parse_args()
    lookback_days = args.days

    fetched_at = datetime.now()
    print(f"[{fetched_at}] Starting Meta Ads fetch for Sarkar "
          f"({len(AD_ACCOUNT_IDS)} account(s), last {lookback_days} days)...")

    con = get_conn()
    init_tables(con)
    migrate_existing_tables(con)

    status = "success"
    n_campaign = n_ad = n_creative = n_funnel = 0

    try:
        for ad_account_id in AD_ACCOUNT_IDS:
            print(f"  -- Account {ad_account_id} --")

            campaign_rows = fetch_campaign_insights(ad_account_id, lookback_days)
            upsert_campaign_insights(con, campaign_rows, fetched_at)
            n_campaign += len(campaign_rows)
            print(f"     Campaign insights: {len(campaign_rows)} rows")

            account_daily_rows = fetch_account_daily_insights(ad_account_id, lookback_days)
            funnel_rows = build_daily_funnel(account_daily_rows)
            upsert_daily_funnel(con, funnel_rows, fetched_at)
            n_funnel += len(funnel_rows)
            print(f"     Daily funnel: {len(funnel_rows)} rows")

            ad_rows = fetch_ad_level_data(ad_account_id, lookback_days)
            upsert_ad_insights(con, ad_rows, fetched_at)
            n_ad += len(ad_rows)
            print(f"     Ad insights: {len(ad_rows)} rows")

            creative_rows = fetch_creatives(ad_account_id)
            upsert_creatives(con, creative_rows, fetched_at)
            n_creative += len(creative_rows)
            print(f"     Creatives: {len(creative_rows)} rows")

    except Exception as e:
        status = f"error: {e}"
        print(f"  FAILED: {e}")

    con.execute("""
        INSERT INTO fetch_log (fetched_at, ad_accounts, lookback_days, rows_campaign, rows_ad, rows_creative, rows_funnel, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [fetched_at, ",".join(AD_ACCOUNT_IDS), lookback_days, n_campaign, n_ad, n_creative, n_funnel, status])

    con.close()
    print(f"[{datetime.now()}] Done. Status: {status} "
          f"(total: {n_campaign} campaign rows, {n_ad} ad rows, {n_creative} creatives, "
          f"{n_funnel} funnel rows across {len(AD_ACCOUNT_IDS)} account(s))")


if __name__ == "__main__":
    main()
