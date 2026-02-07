---
trigger: DRIVE_FETCH
result_label: Drive Fetch
result_description: Google Drive file search results
handler: drive_fetch
---

## Routing

GOOGLE DRIVE FETCH: When user wants to find and view a file from Google Drive:

Respond with: DRIVE_FETCH: <search term>

Examples:
- User: "Show me my OPT I-20 from Google Drive"
  You: DRIVE_FETCH: OPT I-20

- User: "Find my resume on Drive"
  You: DRIVE_FETCH: resume

---

## Workflow

### Objective
Search Google Drive for a file by name and return its view link so the user can open it in their browser.

### Step 1: Detect File Request
When the user asks to find or show a file from Google Drive, the root agent responds with `DRIVE_FETCH: <search term>`.

### Step 2: Search Execution
The orchestrator calls `drive_agent`'s `search_files()`:
1. Searches by partial name matching (case-insensitive)
2. Returns file names and webViewLinks
3. Auto-opens first result in browser

### Step 3: Format Response
After receiving the tool result:
- If found: "Here's your [filename]: [link]"
- If multiple matches: List them with links
- If not found: "I couldn't find any files matching that name in your Drive."

### Notes
- Returns up to 10 matching files
- Links open in Google Drive's web viewer
