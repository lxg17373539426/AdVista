# Report Frontend Skill

## Purpose

Turn evidence-grounded advertising analysis into an offline, decision-ready HTML report.

## Design Direction

- Audience: brand, growth, creative, and compliance reviewers.
- Tone: editorial-industrial, credible, calm, and scan-friendly.
- Visual language: dark green navigation, warm paper canvas, restrained red-orange accents.
- Priority: executive summary, selling points, audit status, then inspectable evidence.
- Memorable detail: every claim carries a visible audit state and a directly adjacent evidence chain.

## Rendering Rules

- Keep the HTML self-contained. Do not require external fonts, scripts, images, or CDNs.
- Preserve every claim, evidence reference, confidence value, audit finding, and evidence gap.
- Use semantic landmarks and headings in a logical order.
- Use a sticky desktop contents rail and a horizontally scrollable mobile contents bar.
- Keep body copy readable, with a maximum line length and clear contrast.
- Treat selling points as the primary section without hiding other analysis dimensions.
- Show evidence images in stable aspect-ratio tiles with timestamps and source metadata.
- Avoid gradients, glass effects, oversized rounded cards, decorative blobs, and generic dashboard chrome.
- Support narrow screens and printable output without horizontal page overflow.

## Safety

- Escape all model-generated and source-derived text before inserting it into HTML.
- Do not emit executable JavaScript.
- Do not turn unsupported model output into visual certainty.
