---
trigger: PAYCHECK_PROCESSING
result_label: Paycheck Processing
result_description: Sheets/Drive confirmation
handler: paycheck
---

## Routing

PAYCHECK PROCESSING: When user asks to process a paycheck or paystub PDF:

Respond with: PAYCHECK_PROCESSING:

The paycheck skill will automatically:
1. Extract PDF text using docs_agent
2. Parse paycheck data (Pay Period, Gross, Taxes, Net, Hours)
3. Update Google Sheet via sheets_agent
4. Upload PDF to Drive via drive_agent

Example:
- User: "Process this paycheck" (with PDF attached)
  You: PAYCHECK_PROCESSING:

---

## Workflow

### Objective
Extract structured paycheck data from PDF text, update Google Sheets with the data, and archive the PDF to Google Drive.

### Required State
- `pdf_bytes`: Raw PDF file content
- `pdf_filename`: Original filename of the PDF

### Step 1: Extract Structured Data
From the PDF text content provided by the user, extract the following 11 fields in order:

1. **Pay Period** - Format as "Mon DD - Mon DD" (e.g., "Nov 16 - Nov 30")
2. **Gross Pay** - Total earnings before deductions
3. **Social Security** - Social Security tax withheld (use 0.00 if "Exempt")
4. **Medicare** - Medicare tax withheld (use 0.00 if "Exempt")
5. **Federal Income Tax** - Federal tax withheld
6. **NY Income Tax** - New York state tax withheld
7. **NY PFL** - NY Paid Family Leave deduction
8. **NY Disability** - NY Disability insurance deduction
9. **Total Deductions** - Sum of tax withholdings ONLY (exclude 401k, voluntary deductions)
10. **Net Pay** - Take-home pay after all deductions
11. **Hours** - Total hours worked during pay period

### Step 2: Google Sheets Update
The orchestrator routes to `sheets_agent`:
- Appends CSV row to spreadsheet at `PAYCHECK_SHEET_ID`
- Default sheet name: "Sheet1", starting at column A

### Step 3: Google Drive Upload
The orchestrator routes to `drive_agent`:
- Uploads PDF to folder at `PAYCHECK_FOLDER_ID`
- Uses original filename

### Error Handling
- If extraction fails: ask user to verify PDF contains all required fields
- If Sheets/Drive fails: inform user which operation failed

### Notes
- Only extract numeric values (no currency symbols)
- Pay periods should always be in "Mon DD - Mon DD" format
- Total Deductions = sum of taxes only (lines 3-8), NOT including retirement contributions
