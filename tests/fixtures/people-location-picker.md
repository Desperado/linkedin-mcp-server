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

## Second attempt by place (added 2026-10-07)

LinkedIn's typeahead matches its own labels, so a plan label with a country but
no region ("Munich, Germany") finds nothing, while the place alone ("Munich")
lists the canonical "Munich, Bavaria, Germany" next to other places called
Munich and a metropolitan area. When the typed label finds no suggestion or
more than one, the picker now retypes the place alone, reads only the visible
buttons whose text starts with it, and ranks them by fixed rules: the exact
label, else the one suggestion whose first part is the place, whose last part
is the requested country and whose region matches when one was named; regions
("… Area") only when asked for. Comparison folds case, accents and whitespace
and never accepts a prefix. Anything but exactly one winner keeps the picker
closed with the first attempt's verdict. Production run `028cea6e` on
2026-10-07 failed on "Munich, Germany" with `location_unresolved` before this.

Before typing the place, the picker clears the field and requires the first
attempt's suggestions to disappear, so they are never read as the place's
offer. The click is bound to the winning label rather than to its position:
the typeahead can redraw between the read and the click, and a position would
then name another place, which Lynx would remember for the plan's label. The
place must be followed by whitespace, a comma or the end of the text, because
JavaScript's `\b` is ASCII-only and never ends a place such as "Malmö".
