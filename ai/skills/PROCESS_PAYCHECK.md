# Paycheck Processing Skill

## Trigger
This skill activates when the user uploads a paycheck PDF or mentions processing a paycheck/paystub.

## Objective
Extract structured paycheck data from PDF text, update Google Sheets with the data, and archive the PDF to Google Drive.

## Required State
- `pdf_bytes`: Raw PDF file content
- `pdf_filename`: Original filename of the PDF

## Workflow Instructions

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

Format your response as:
```
PAYCHECK_PROCESSING:
Pay Period,Gross Pay,Social Security,Medicare,Federal Income Tax,NY Income Tax,NY PFL,NY Disability,Total Deductions,Net Pay,Hours
Nov 16 - Nov 30,1000.00,0.00,0.00,37.50,31.19,3.88,1.30,73.87,926.13,40
```

### Step 2: Google Sheets Update
**DO NOT execute this yourself** - The orchestrator will route to `sheets_agent`:

- The CSV data will be passed to `sheets_agent`
- Agent will append the row to the spreadsheet at `PAYCHECK_SHEET_ID`
- Default sheet name: "Sheet1", starting at column A

### Step 3: Google Drive Upload
**DO NOT execute this yourself** - The orchestrator will route to `drive_agent`:

- The PDF file bytes will be passed to `drive_agent`
- Agent will upload to the folder at `PAYCHECK_FOLDER_ID`
- File will be named using the original filename

### Step 4: Confirmation
After both operations complete, you will receive a system message with results:
```
[TOOL RESULT - Paycheck Processing]
Successfully appended row to Google Sheets...
Successfully uploaded 'paycheck.pdf' to Google Drive...
```

Format this naturally for the user, keeping it SHORT (1-3 sentences):
- "Done! I've added your paycheck to Sheets and archived the PDF to Drive."
- "Your paycheck has been processed and saved."

## Error Handling

If extraction fails:
- Ask the user to verify the PDF contains all required fields
- Request a clearer copy if text extraction is incomplete

If Sheets/Drive operations fail:
- The system message will indicate the error
- Inform the user which operation failed and suggest checking credentials

## Notes
- Only extract numeric values (no currency symbols)
- Pay periods should always be in "Mon DD - Mon DD" format
- Total Deductions = sum of taxes only (lines 3-8), NOT including retirement contributions
- This is a SINGLE-USER system - all data goes to the same Sheets/Drive location
