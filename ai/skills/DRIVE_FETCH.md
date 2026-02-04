# Drive Fetch Skill

## Trigger
This skill activates when the user wants to find and view a file from Google Drive.

Examples:
- "Show me my OPT I-20 from Google Drive"
- "Find my resume on Drive"
- "Get the project proposal from Drive"

## Objective
Search Google Drive for a file by name and return its view link so the user can open it in their browser.

## Workflow Instructions

### Step 1: Detect File Request
When the user asks to find or show a file from Google Drive, respond with:
```
DRIVE_FETCH: <search term>
```

Example:
- User: "Show me my OPT I-20 from Google Drive"
- You: DRIVE_FETCH: OPT I-20

### Step 2: Search Execution
**Handled by orchestrator** - The search term is passed to `drive_agent` which:
1. Calls `search_files(name=<search term>)`
2. Returns file names and their webViewLinks

### Step 3: Format Response
After receiving the tool result with file links, format naturally:
- If found: "Here's your [filename]: [link]"
- If multiple matches: List them with links
- If not found: "I couldn't find any files matching that name in your Drive."

## Notes
- Search uses partial name matching (case-insensitive)
- Returns up to 10 matching files
- Links open in Google Drive's web viewer
