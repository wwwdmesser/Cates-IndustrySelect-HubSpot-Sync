# Cates IndustrySelect → HubSpot Sync

Automatically sync manufacturing companies and contacts from IndustrySelect into HubSpot using the IndustrySelect API, with filtering, deduplication, and scheduled GitHub Actions workflows.

## Overview

This project was built to automate prospecting for industrial and manufacturing companies.

Using the IndustrySelect API, the script:

- Retrieves new and updated company records
- Filters contacts by relevant manufacturing job titles
- Creates or updates records in HubSpot
- Exports synced records to CSV as a backup
- Maintains sync state so interrupted runs can resume where they left off
- Runs automatically on a weekly schedule through GitHub Actions

The primary use case is keeping HubSpot populated with qualified manufacturing contacts for sales and marketing campaigns.

---

## Features

### IndustrySelect API Integration

- Connects to the IndustrySelect REST API
- Pulls both new and updated company records
- Uses incremental sync dates to avoid duplicate processing
- Handles API rate limiting with exponential backoff

### Contact Filtering

Filters contacts based on manufacturing and plant leadership roles, including:

- Plant Manager
- Operations
- Facilities
- Facility Manager
- Maintenance
- Engineering
- Plant Engineer
- Quality Control
- Quality Assurance
- QA Manager
- Production Manager

Additional titles can easily be added to the filter list.

### HubSpot Integration

Creates and updates:

- Companies
- Contacts

Uses the official HubSpot Python SDK and private app access tokens.

### Sync State Persistence

The project maintains a `sync_state.json` file containing:

- Last sync date
- Current sync phase
- Company IDs processed
- Contact lists
- HubSpot push status

This allows interrupted syncs to resume without starting over.

### GitHub Actions Automation

Runs automatically every Monday at:

- **7:00 AM Central Time**
- **12:00 UTC**

Also supports manual execution from the GitHub Actions tab.

---

## Project Structure

```text
Cates_IndustrySelect_HubSpot_Sync/
│
├── hubspot_sync.py          # Main sync script
├── hubspot_sync.yml         # GitHub Actions workflow
├── requirements.txt         # Python dependencies
├── sync_state.json          # Sync progress state
└── README.md
```

---

## Requirements

- Python 3.11+
- IndustrySelect API Key
- HubSpot Private App Access Token

---

## Installation

Clone the repository:

```bash
git clone https://github.com/<username>/Cates_IndustrySelect_HubSpot_Sync.git

cd Cates_IndustrySelect_HubSpot_Sync
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Environment Variables

Create a `.env` file:

```env
INDUSTRYSELECT_KEY=your_industryselect_api_key

HUBSPOT_ACCESS_TOKEN=your_hubspot_private_app_token
```

The script automatically loads these values using `python-dotenv`.

---

## Running the Sync

Run manually:

```bash
python hubspot_sync.py
```

The script will:

1. Load the last sync state
2. Retrieve new and updated companies from IndustrySelect
3. Filter contacts by title
4. Push companies and contacts to HubSpot
5. Export a CSV backup
6. Save the updated sync state

---

## GitHub Actions

The included GitHub Actions workflow:

- Runs automatically every Monday
- Supports manual execution (`workflow_dispatch`)
- Uploads CSV backups as artifacts
- Caches `sync_state.json`
- Resumes interrupted syncs automatically

### Required GitHub Secrets

Configure the following repository secrets:

| Secret | Description |
|-------|-------------|
| `INDUSTRYSELECT_KEY` | IndustrySelect API key |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot Private App access token |

---

## Rate Limiting

IndustrySelect enforces API rate limits.

This project includes:

- Automatic detection of HTTP 429 responses
- Exponential backoff retry logic
- Multiple retry attempts before failing

This helps keep large sync operations reliable.

---

## CSV Backup

Each sync exports a dated CSV file:

```text
hubspot_sync_YYYY-MM-DD.csv
```

CSV files are automatically uploaded as GitHub Action artifacts and retained for 30 days.

---

## Future Improvements

Potential enhancements:

- Bidirectional HubSpot ↔ IndustrySelect sync
- Configurable title filters via JSON/YAML
- Industry and SIC/NAICS filtering
- Company deduplication using HubSpot search APIs
- Slack or email notifications for failed syncs
- Docker containerization

---

## License

This project is intended for internal business use.

