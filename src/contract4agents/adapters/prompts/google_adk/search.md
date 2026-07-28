Use Google Search to answer the supplied query.

Return only JSON with this exact shape:

{"results":[{"title":"string","url":"string","snippet":"string"}]}

Every result must contain a source title, its direct URL, and a factual snippet
supported by that source. Do not invent missing fields. If no complete results
are available, return {"results":[]}. Do not wrap the JSON in Markdown or add
explanatory text.
