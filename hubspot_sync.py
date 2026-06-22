import os
import time
import json
import csv
from datetime import date, timedelta

import requests
from dotenv import load_dotenv
from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactInput
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyInput
from hubspot.crm.contacts.exceptions import ApiException as ContactApiException
from hubspot.crm.companies.exceptions import ApiException as CompanyApiException

# -----------------------------
# CONFIG
# -----------------------------
load_dotenv()

INDUSTRYSELECT_KEY = os.environ.get("INDUSTRYSELECT_KEY")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_ACCESS_TOKEN")

if not INDUSTRYSELECT_KEY or not HUBSPOT_TOKEN:
    print("ERROR: Missing INDUSTRYSELECT_KEY or HUBSPOT_ACCESS_TOKEN in .env file.")
    exit()

BASE_URL = "https://www.industryselect.com/api/v1"
SYNC_STATE_FILE = "sync_state.json"
OUTPUT_FILE = f"hubspot_sync_{date.today().isoformat()}.csv"
MAX_DAILY_RECORDS = 9500

SALES_LOW = 50_000_000
SALES_HIGH = 40_000_000_000

is_headers = {
    "X-API-Key": INDUSTRYSELECT_KEY,
    "Content-Type": "application/json"
}

hs_client = HubSpot(access_token=HUBSPOT_TOKEN)


# -----------------------------
# RATE LIMITED REQUEST WRAPPERS (IndustrySelect)
# -----------------------------
def safe_post(url, payload, max_retries=5):
    delay = 2
    for attempt in range(max_retries):
        r = requests.post(url, headers=is_headers, json=payload)
        if r.status_code == 200:
            return r
        if r.status_code == 429:
            print(f"  429 hit → backing off {delay}s (attempt {attempt+1})")
            time.sleep(delay)
            delay *= 2
            continue
        print(f"  POST ERROR {r.status_code}: {r.text}")
        return None
    print("  Max retries exceeded.")
    return None


def safe_get(url, params, max_retries=5):
    delay = 2
    for attempt in range(max_retries):
        r = requests.get(url, headers=is_headers, params=params)
        if r.status_code == 200:
            return r
        if r.status_code == 429:
            print(f"  429 hit → backing off {delay}s (attempt {attempt+1})")
            time.sleep(delay)
            delay *= 2
            continue
        print(f"  GET ERROR {r.status_code}: {r.text}")
        return None
    print("  Max retries exceeded.")
    return None


# -----------------------------
# TITLE FILTER
# -----------------------------
TARGET_TITLE_KEYWORDS = [
    "plant manager",
    "operations",
    "facilities",
    "facility",
    "maintenance",
    "engineering",
    "engineer",
    "quality control",
    "quality assurance",
    "qa manager",
    "plant operations",
    "production manager",
]

def title_matches(title):
    if not title:
        return False
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in TARGET_TITLE_KEYWORDS)


# -----------------------------
# HELPERS
# -----------------------------
def chunks(lst, size=100):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


def load_sync_state():
    """Load existing sync state, or create a fresh one if missing."""
    if os.path.exists(SYNC_STATE_FILE):
        with open(SYNC_STATE_FILE) as f:
            return json.load(f)

    print(f"No {SYNC_STATE_FILE} found — creating a fresh one.")
    fresh_state = build_fresh_state()
    save_sync_state(fresh_state)
    return fresh_state


def build_fresh_state(last_sync_date=None):
    """Return a clean sync state dict, defaulting last_sync_date to 7 days ago."""
    if last_sync_date is None:
        last_sync_date = (date.today() - timedelta(days=7)).isoformat()
    return {
        "last_sync_date": last_sync_date,
        "phase": "list_new",
        "new_company_ids": [],
        "updated_company_ids": [],
        "all_company_ids": [],
        "batch_index": 0,
        "all_contacts": [],
        "pushed_to_hubspot": False
    }


def reset_sync_state():
    """Reset sync_state.json for the next scheduled run (date = today)."""
    fresh = build_fresh_state(last_sync_date=date.today().isoformat())
    save_sync_state(fresh)
    print(f"  sync_state.json reset for next run (last_sync_date = {fresh['last_sync_date']})")


def save_sync_state(state):
    with open(SYNC_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  Sync state saved to {SYNC_STATE_FILE}")


def pull_company_ids(since_date, since_field, max_records, already_used=0):
    company_ids = []
    page = 1
    total_pages = None

    while True:
        payload = {
            "naics": ["311"],
            "sales": {"low": SALES_LOW, "high": SALES_HIGH},
            since_field: since_date,
            "profile": "preview",
            "page": page,
            "pagesize": 500
        }

        r = safe_post(f"{BASE_URL}/list/", payload)
        if not r:
            print(f"  {since_field} request failed — quota likely exhausted.")
            return company_ids, True

        data = r.json()

        if total_pages is None:
            total_pages = data.get("total_pages", 1)
            total_count = data.get("total", "?")
            print(f"  {total_count} companies ({since_field} {since_date}) across {total_pages} pages")

        results = data.get("results", [])
        if not results:
            break

        for c in results:
            company_ids.append(c["Company ID"])

        used = already_used + len(company_ids)
        print(f"  Page {page}/{total_pages} → {len(company_ids)} found | {used} records used today")

        if used >= max_records:
            print(f"  Approaching daily record limit. Stopping {since_field} pull.")
            return company_ids, True

        if page >= total_pages:
            break

        page += 1
        time.sleep(2.1)

    return company_ids, False


# -----------------------------
# HUBSPOT FUNCTIONS
# -----------------------------
def find_company_by_industryselect_id(company_id):
    try:
        result = hs_client.crm.companies.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "industryselect_company_id",
                        "operator": "EQ",
                        "value": str(company_id)
                    }]
                }],
                "properties": ["name", "industryselect_company_id"],
                "limit": 1
            }
        )
        if result.total > 0:
            return result.results[0]
        return None
    except CompanyApiException as e:
        print(f"  Company search error: {e}")
        return None


def upsert_company(company_id, company_name):
    existing = find_company_by_industryselect_id(company_id)

    if existing:
        current_name = existing.properties.get("name")
        if not current_name and company_name:
            try:
                hs_client.crm.companies.basic_api.update(
                    company_id=existing.id,
                    simple_public_object_input={"properties": {"name": company_name}}
                )
                print(f"    Updated company {company_name} (was blank)")
            except CompanyApiException as e:
                print(f"    Company update error: {e}")
        return existing.id
    else:
        try:
            new_company = hs_client.crm.companies.basic_api.create(
                simple_public_object_input_for_create=CompanyInput(
                    properties={
                        "name": company_name,
                        "industryselect_company_id": str(company_id)
                    }
                )
            )
            print(f"    Created company: {company_name}")
            return new_company.id
        except CompanyApiException as e:
            print(f"    Company create error: {e}")
            return None


def find_contact_by_email(email):
    if not email:
        return None
    try:
        result = hs_client.crm.contacts.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "email",
                        "operator": "EQ",
                        "value": email
                    }]
                }],
                "properties": ["email", "firstname", "lastname", "jobtitle", "phone"],
                "limit": 1
            }
        )
        if result.total > 0:
            return result.results[0]
        return None
    except ContactApiException as e:
        print(f"  Contact search error: {e}")
        return None


FIELD_MAP = {
    "first_name": "firstname",
    "last_name": "lastname",
    "title": "jobtitle",
    "phone": "phone",
}


def upsert_contact(contact_data, company_hs_id):
    email = contact_data.get("email")

    if not email:
        print(f"    Skipping (no email): {contact_data.get('first_name')} {contact_data.get('last_name')}")
        return

    existing = find_contact_by_email(email)

    if existing:
        updates = {}
        for source_field, hs_field in FIELD_MAP.items():
            new_value = contact_data.get(source_field)
            current_value = existing.properties.get(hs_field)
            if new_value and not current_value:
                updates[hs_field] = new_value

        if updates:
            try:
                hs_client.crm.contacts.basic_api.update(
                    contact_id=existing.id,
                    simple_public_object_input={"properties": updates}
                )
                print(f"    Updated {email} → filled: {list(updates.keys())}")
            except ContactApiException as e:
                print(f"    Contact update error: {e}")
        else:
            print(f"    Skipped {email} (no blank fields to fill)")

        contact_hs_id = existing.id
    else:
        properties = {"email": email}
        for source_field, hs_field in FIELD_MAP.items():
            value = contact_data.get(source_field)
            if value:
                properties[hs_field] = value

        try:
            new_contact = hs_client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=ContactInput(properties=properties)
            )
            print(f"    Created: {email}")
            contact_hs_id = new_contact.id
        except ContactApiException as e:
            print(f"    Contact create error: {e}")
            return

    if company_hs_id:
        try:
            hs_client.crm.associations.v4.basic_api.create(
                object_type="contacts",
                object_id=contact_hs_id,
                to_object_type="companies",
                to_object_id=company_hs_id,
                association_spec=[{
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 1
                }]
            )
        except Exception:
            pass  # association likely already exists


def push_to_hubspot(all_contacts):
    print("\n" + "=" * 50)
    print("PHASE 4: PUSHING TO HUBSPOT")
    print("=" * 50)

    company_id_cache = {}

    for i, contact in enumerate(all_contacts):
        company_id = contact["company_id"]
        company_name = contact["company_name"]

        if company_id not in company_id_cache:
            print(f"  Company: {company_name}")
            hs_company_id = upsert_company(company_id, company_name)
            company_id_cache[company_id] = hs_company_id
        else:
            hs_company_id = company_id_cache[company_id]

        upsert_contact(contact, hs_company_id)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(all_contacts)} contacts")

        time.sleep(0.15)

    print(f"\nHubSpot push complete. Processed {len(all_contacts)} contacts.")


# -----------------------------
# MAIN PIPELINE
# -----------------------------
def main():
    state = load_sync_state()

    if state["last_sync_date"] is None:
        print("ERROR: No last_sync_date set in sync_state.json.")
        exit()

    since_date = state["last_sync_date"]
    print(f"Syncing changes since: {since_date}\n")

    # ---- PHASE 1: newly added companies ----
    if state["phase"] == "list_new":
        print("=" * 50)
        print("PHASE 1: NEW COMPANIES (addedsince)")
        print("=" * 50)

        ids, quota_hit = pull_company_ids(since_date, "addedsince", MAX_DAILY_RECORDS)
        state["new_company_ids"] = ids

        if quota_hit:
            save_sync_state(state)
            print("Quota hit during PHASE 1. Resume tomorrow by re-running this script.")
            return

        state["phase"] = "list_updated"
        save_sync_state(state)

    # ---- PHASE 2: updated companies ----
    if state["phase"] == "list_updated":
        print("\n" + "=" * 50)
        print("PHASE 2: UPDATED COMPANIES (updatedsince)")
        print("=" * 50)

        already_used = len(state["new_company_ids"])
        ids, quota_hit = pull_company_ids(since_date, "updatedsince", MAX_DAILY_RECORDS, already_used)
        state["updated_company_ids"] = ids

        if quota_hit:
            save_sync_state(state)
            print("Quota hit during PHASE 2. Resume tomorrow by re-running this script.")
            return

        combined = list(set(state["new_company_ids"] + state["updated_company_ids"]))
        state["all_company_ids"] = combined
        state["phase"] = "batch"
        save_sync_state(state)
        print(f"\nTotal unique companies to process: {len(combined)}")

    # ---- PHASE 3: batch fetch contacts ----
    if state["phase"] == "batch":
        print("\n" + "=" * 50)
        print("PHASE 3: FETCHING CONTACTS VIA BATCH")
        print("=" * 50)

        all_company_ids = state["all_company_ids"]
        all_contacts = state["all_contacts"]
        start_batch = state["batch_index"]
        chunk_list = list(chunks(all_company_ids, 100))

        print(f"Resuming from batch {start_batch + 1}/{max(len(chunk_list),1)}")

        quota_hit = False

        for idx, chunk in enumerate(chunk_list):
            if idx < start_batch:
                continue

            company_ids_str = ",".join(chunk)
            r = safe_get(f"{BASE_URL}/batch/", params={"companyids": company_ids_str})

            if not r:
                print(f"  Batch {idx+1} failed — quota likely exhausted.")
                state["batch_index"] = idx
                state["all_contacts"] = all_contacts
                save_sync_state(state)
                quota_hit = True
                break

            data = r.json()

            for company in data.get("results", []):
                company_name = company.get("Company")
                company_id = company.get("Company ID")

                for e in company.get("Executives", []):
                    title = e.get("Title")
                    if not title_matches(title):
                        continue

                    all_contacts.append({
                        "company_id": company_id,
                        "company_name": company_name,
                        "first_name": e.get("First Name"),
                        "last_name": e.get("Last Name"),
                        "title": title,
                        "email": e.get("Direct Email"),
                        "phone": e.get("Direct Phone"),
                        "is_new_company": company_id in state["new_company_ids"]
                    })

            print(f"  Batch {idx+1}/{len(chunk_list)} done → matching contacts: {len(all_contacts)}")

            if (idx + 1) % 10 == 0:
                state["batch_index"] = idx + 1
                state["all_contacts"] = all_contacts
                save_sync_state(state)

            time.sleep(2.1)

        if quota_hit:
            print("Resume tomorrow by re-running this script.")
            return

        state["batch_index"] = len(chunk_list)
        state["all_contacts"] = all_contacts
        state["phase"] = "export"
        save_sync_state(state)

    # ---- PHASE 4: export CSV (backup record) ----
    if state["phase"] == "export":
        print("\n" + "=" * 50)
        print("EXPORTING BACKUP CSV")
        print("=" * 50)

        fieldnames = [
            "company_id", "company_name", "first_name", "last_name",
            "title", "email", "phone", "is_new_company"
        ]

        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(state["all_contacts"])

        print(f"Backup saved to {OUTPUT_FILE}")
        print(f"New companies: {len(state['new_company_ids'])}")
        print(f"Updated companies: {len(state['updated_company_ids'])}")

        state["phase"] = "push"
        save_sync_state(state)

    # ---- PHASE 5: push to HubSpot ----
    if state["phase"] == "push":
        push_to_hubspot(state["all_contacts"])
        state["phase"] = "done"
        state["pushed_to_hubspot"] = True
        save_sync_state(state)

    # ---- DONE: auto-reset for next run ----
    if state["phase"] == "done":
        total_contacts = len(state["all_contacts"])
        new_cos = len(state.get("new_company_ids", []))
        updated_cos = len(state.get("updated_company_ids", []))

        print("\n" + "=" * 50)
        print("SYNC COMPLETE")
        print("=" * 50)
        print(f"Total contacts synced : {total_contacts}")
        print(f"New companies         : {new_cos}")
        print(f"Updated companies     : {updated_cos}")

        # Auto-reset so the next scheduled run starts clean
        print("\nResetting sync state for next run...")
        reset_sync_state()

        print("\nAll done. No manual steps required.")


if __name__ == "__main__":
    main()
