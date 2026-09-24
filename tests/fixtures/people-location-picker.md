# People location picker controls

Observed on 2026-09-24 in a visible local Chrome for Testing session using the
owner's account. Only filter controls, location suggestions, and filter query
parameters were recorded; no result cards or session data are included.

```json
{
  "all_filters": "All filters",
  "locations_button": "Locations",
  "entry_button": "Add a location",
  "entry_placeholder": "Add a location",
  "reset_button": "Reset",
  "suggestion_role": "button",
  "berlin_suggestion_inner_text": "Berlin, Germany",
  "ambiguous_berlin_examples": [
    "Berlin, Germany",
    "Berlin, Connecticut, United States",
    "Berlin, Maryland, United States"
  ],
  "apply_button": null
}
```

The first People page load exposed **All filters** but no visible **Locations**
button. Opening **All filters** exposed **Locations** and an **Add a location**
button. Clicking the latter produced a textbox with the placeholder above. On
a later People page load, **Locations** was directly visible in the compact
filter bar; clicking it produced the textbox, a **Reset** button, and location
checkboxes. The textbox had no `role`, `aria-controls`, or `aria-expanded`
attribute of its own.

Typing `Berlin, Germany` produced a visible suggestion button whose
`innerText` was that exact label; the button had no `aria-label`. Its computed
accessible name was not measured. Typing `Berlin` produced the Berlin
suggestions listed above and others, so the short input was ambiguous. Typing
`Munich, Germany` produced no matching suggestion during this observation;
that result is not assumed to hold across other sessions. Suggestion text also
appeared in plain spans with no explicit role. Text locators over the whole
page can include locations in search cards behind the picker; the suggestion
button is the reliable target. A subsequent MCP call using the button's
assumed accessible name failed with `LinkedIn showed no location suggestion`.
The button's observed `innerText` is the selector property in the revised
candidate; that revision has not had a live call.

The owner's manually applied Berlin filter was observed in the People URL with
`keywords=CTO`, `geoUrn=["103035651"]`, `network=["F"]`, and
`origin=FACETED_SEARCH`; `sid` was absent. The apply control's exact accessible
name was not established in this observation.
