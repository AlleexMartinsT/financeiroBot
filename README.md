# Finance Bot

Finance Bot automates the processing of XML invoices from Gmail and writes payable entries to Google Sheets.

## What It Does

- Connects to two Gmail accounts ("Conta Principal" and "Conta NFe").
- Finds invoice XML attachments (`NF-e` and `CT-e`).
- Parses suppliers, values, due dates, and installments.
- Writes entries to the correct company/year worksheet.
- Creates monthly tabs automatically (for example: `Feb/2026`).
- Prevents duplicate launches by invoice number + due date.
- Handles Braspress-specific CT-e logic and invoice matching.
- Generates daily reports with:
  - processed suppliers,
  - ignored suppliers,
  - warning list with daily occurrence counts.

## Recent Improvements

- Automatic startup: the verification loop now starts without clicking "start" in the tray.
- Gmail optimization:
  - period-based query (default: last 30 days),
  - pagination,
  - avoids reprocessing by using `XML Processado` and `XML Analisado` labels.
- Google Sheets optimization:
  - reduced redundant reads before writes,
  - better worksheet creation race handling (`already exists`).
- Report reliability:
  - fixed daily `.tmp` report consolidation.
- Startup/cycle hygiene:
  - cleans stale files in `xmls_baixados` before each verification cycle.

## Project Structure

- `main.py`: application loop, scheduler, tray integration.
- `gmail_fetcher.py`: Gmail search/download flow.
- `processor.py`: XML parsing and Google Sheets insertion.
- `braspress_utils.py`: Braspress retrieval/insert helpers.
- `reporter.py`: daily report and warnings aggregation.
- `auth.py`: Gmail and Sheets authentication.
- `config.py`: private config loading and constants.

## Requirements

- Python 3.11+
- Google API credentials for Gmail and Sheets
- Service account access to target spreadsheets
- Optional: Playwright dependencies for Braspress login flow

## Required Files

Create these files under `secrets/`:

- `config_privado.json`
- `credentials.json`
- `credentials_gmail.json`
- `credentials_gmailNFE.json`
- `braspress_config.json` (if Braspress flow is enabled)

## Basic Run

```bash
python main.py
```

The app starts the verification loop automatically and also opens the tray icon menu.

## Gmail Labels Used

- `XML Processado`: email had at least one XML successfully inserted into Sheets.
- `XML Analisado`: email was analyzed and should not be scanned again.

## Time Window for Email Scan

In `gmail_fetcher.py`:

- `FILTRO_PERIODO_EMAILS = "last_30_days"` (default)
- `FILTRO_PERIODO_EMAILS = "current_and_previous_month"`

## Notes

- Year selection is based on XML due date, not email receive date.
- Duplicate prevention is enforced at worksheet level.
- Daily reports are written in `relatorios/`.
