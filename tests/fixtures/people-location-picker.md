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
  "hamburg_suggestion_inner_text": "Hamburg, Germany",
  "ambiguous_berlin_examples": [
    "Berlin, Germany",
    "Berlin, Connecticut, United States",
    "Berlin, Maryland, United States"
  ],
  "apply_role": "link",
  "apply_name": "Show results"
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
button is the reliable target. MCP calls using the button's assumed accessible
name and then its observed `innerText` both failed with `LinkedIn showed no
location suggestion`. In a later bounded browser trace, the exact text locator
matched one visible suggestion and clicking it exposed the apply link. This
initial mismatch was traced to the adapter filling the page's last textbox
instead of the newly opened location textbox.

In a separate bounded inspection, `Hamburg, Germany` yielded one exact
suggestion. The shorter `Hamburg` input yielded several different places.
Local MCP calls with the corrected controls resolved Berlin and Hamburg to
numeric `geoUrn` values `103035651` and `101949806` respectively.
`Munich, Germany` did not yield an exact or single
`Munich, <region>, Germany` suggestion in these observations; the picker must
still refuse to choose a different district or metro area.

The **All filters** panel's apply control was a visible link named **Show
results**, not a button. Clicking it after the one exact Berlin suggestion
produced the native numeric `geoUrn` shown below. The compact layout's apply
role was not observed.

The owner's manually applied Berlin filter was observed in the People URL with
`keywords=CTO`, `geoUrn=["103035651"]`, `network=["F"]`, and
`origin=FACETED_SEARCH`; `sid` was absent. Later local MCP calls carried the
same filter parameter names.
