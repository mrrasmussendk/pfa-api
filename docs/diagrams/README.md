# Documentation diagrams

The documentation embeds SVG images so readers do not need Mermaid support in their Markdown viewer. Each image has an editable Mermaid `.mmd` source beside it, linked from the page that uses it.

Edit the `.mmd` source and regenerate its matching `.svg` with a Mermaid renderer. Keep HTML labels disabled, use a white background, and retain the SVG viewBox so the image scales cleanly. The diagrams use native SVG text, with no JavaScript, remote fonts, or external assets.

Shared diagrams (the harness, context budget, and dependency graph) reuse the same image and source across pages. Update the pair once to update every reference.
