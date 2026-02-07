---
trigger: WORK_TRANSIT
result_label: Work/Home Transit
result_description: Public transit routes with departure/arrival times
handler: work_transit
---

## Routing

WORK/HOME TRANSIT: When user asks about commuting, getting to work, or getting home via public transit:

Respond with: WORK_TRANSIT: <direction>

Examples:
- User: "How do I get to work?"
  You: WORK_TRANSIT: to work

- User: "What's the fastest way home?"
  You: WORK_TRANSIT: to home

- User: "When's the next bus to work?"
  You: WORK_TRANSIT: to work

- User: "How do I get home from work?"
  You: WORK_TRANSIT: to home

Notes:
- Uses the user's Home Address and Work Address from their profile
- Returns real-time transit routes with exact departure/arrival times
- Only use for the user's work commute — for other transit queries, use MAPS:

---

## Workflow

1. Parse Home Address and Work Address from USER_PROFILE.md
2. Determine direction from user message ("to work" vs "to home")
3. Call Google Directions API with mode=transit, departure_time=now
4. Return formatted route with bus/metro lines, departure/arrival times, stops
